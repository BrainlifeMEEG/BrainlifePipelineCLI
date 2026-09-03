#!/usr/bin/env node

const config = require('./config');
const commander = require('commander');
const util = require('./util');
const request = require('request-promise-native'); //replace this with axios

commander
    .option('--id <app id>', 'id of app to run')
    .option('--input <input id>', 'add an input to the application (by input id)', util.collect, [])
    .option('--project <project id>', 'the project to store the output dataset from an app')
    .option('--preferred-resource <resource id>', 'user-preferred resource to use to run an app')
    .option('--branch <resource id>', 'github branch to use to run this app (default: master)')
    .option('--config <json string>', 'config to use for running the app')
    .option('--tag <tag>', 'add a tag to the archived dataset', util.collect, [])
    .option('--instance-name <name>', 'reuse (or create) the instance with this exact name, instead of a random one, so multiple app runs group into a single process')
    .option('--instance-id <id>', 'submit into this exact existing instance by id, instead of finding/creating one by name (takes precedence over --instance-name)')
    .option('--direct-deps', 'skip the /dataset/stage round-trip and depend directly on each input dataset\'s own producing task (via its prov.task/prov.subdir) instead -- avoids an extra staging task and an extra archive read on every chained step; the input dataset still must be status:stored (same validation as before), just referenced in place rather than re-staged. Commander note: this is a plain boolean flag, NOT the special --no-x negation form')
    .option('-j, --json', 'output resulting app task in json format')
    .option('--dry-run', 'print the constructed task submission payload and exit, without POSTing anything')
    .parse(process.argv);

try {
    if(!commander.project) throw new Error("Please specify project id (-p) used to run the app.");
    if(!commander.id) throw new Error("Please specify app id (--id)");
} catch(err) {
    console.error(err.toString());
    process.exit(1);
}

util.loadJwt().then(jwt => {
    let headers = { "Authorization": "Bearer " + jwt };

    runApp(headers, {
        app: commander.id,
        inputs: commander.input,
        project: commander.project,
        resource: commander.preferredResource,
        branch: commander.branch,
        config: commander.config,
        tags: commander.tag,
        json: commander.json,
        instanceName: commander.instanceName,
        instanceId: commander.instanceId,
        directDeps: commander.directDeps,
        dryRun: commander.dryRun,
    }).then(task=>{
        if (commander.json) console.log(JSON.stringify(task, null, 4));
    }).catch(err=>{
        console.error(err);
    });
});

function runApp(headers, opt) {
    return new Promise(async (resolve, reject) => {
        let datatypeTable = {};
        let all_dataset_ids = [];
        let output_metadata = {};
        
        opt.config = opt.config || '{}';
        try {
            opt.config = JSON.parse(opt.config);
        } catch (exception) {
            return reject('Could not parse JSON Config Object');
        }
        
        let datatypes = await util.queryAllDatatypes(headers);
        if (datatypes.length == 0) return reject("couldn't load datatypes");

        let apps = await util.resolveApps(headers, opt.app);
        if (apps.length == 0) return reject("No apps found matching '" + opt.app + "'");
        if (apps.length > 1) return reject("Multiple apps matching '" + opt.app + "'");
        
        let projects = await util.resolveProjects(headers, opt.project);
        if (projects.length == 0) return reject("No projects found matching '" + opt.project + "'");
        if (projects.length > 1) return reject("Multiple projects matching '" + opt.project + "'");
        
        let inputs = {};
        let app = apps[0];
        let project = projects[0];
        let resource;
        
        // check user-inputted branch
        let branch = app.github_branch;
        if (opt.branch) {
            try {
                let branches = await request('https://api.github.com/repos/' + app.github + '/branches', { json: true, headers: { "User-Agent": "brainlife CLI" } });
                let validUserBranch = false;
                branches.forEach(validBranch => {
                    if (opt.branch == validBranch.name) validUserBranch = true;
                });
                
                if (validUserBranch) {
                    branch = opt.branch;
                    if (!opt.json) console.log("Using user-inputted branch: " + branch);
                } else return reject('The given github branch (' + opt.branch + ') does not exist for ' + app.github);
            } catch (err) {
                return reject(err);
            }
        }

        const gids = [project.group_id];
        if(!project.noPublicResource) gids.push(1);
 
        // setting user-preferred resource
        const bestResource = await request(config.api.amaretti + '/resource/best', {
            headers,
            qs: { 
                service: app.github,
                gids,
             },
            json: true
        });
        if (bestResource.resource) resource = bestResource.resource._id;
        if (bestResource.considered && opt.resource) {
            let resources = await util.resolveResources(headers, opt.resource);
            if (resources.length == 0) {
                return reject("No resources found matching '" + resourceSearch + "'");
            }
            if (resources.length > 1) {
                return reject("Multiple resources matching '" + resourceSearch + "'");
            }
            let userResource = resources[0];
            let userResourceIsValid = false;
            bestResource.considered.forEach(resource => {
                if (resource.id == userResource._id) userResourceIsValid = true;
            });
            
            if (userResourceIsValid) {
                if (!opt.json) console.log("Resource " + userResource.name + " (" + userResource._id + ") is valid and will be preferred.");
                resource = userResource._id;
            } else return reject("The given preferred resource (" + userResource.name + ") is unable to run this application");
        }
        
        // create tables to get from id -> appInput and id -> datatype
        let idToAppInputTable = {};
        app.inputs.forEach(input => idToAppInputTable[input.id] = input);
        datatypes.forEach(d => datatypeTable[d._id] = d);
        
        for (let input of opt.inputs) {
            // get dataset for each input
            if (!~input.indexOf(':')) return reject('No key given for dataset ' + input);
            let file_id = input.substring(0, input.indexOf(":"));
            let datasetQuery = input.substring(input.indexOf(":") + 1);
            let datasets = await util.resolveDatasets(headers, datasetQuery);
            
            if (datasets.length == 0) return reject("No data object matching '" + datasetQuery + "'");
            if (datasets.length > 1) return reject("Multiple data objects matching '" + datasetQuery + "'");
            if (all_dataset_ids.indexOf(datasets[0]._id) == -1) all_dataset_ids.push(datasets[0]._id);
            
            let dataset = datasets[0];
            let app_input = idToAppInputTable[file_id];
            
            // validate dataset
            if (dataset.status != "stored") return reject("Input data object " + input + " has storage status '" + dataset.status + "' and cannot be used until it has been successfully stored.");
            if (dataset.removed == true) return reject("Input data object " + input + " has been removed and cannot be used.");
            if (!app_input) return reject("This app's config does not include key '" + file_id + "'");
            if (app_input.datatype != dataset.datatype) return reject("Given input of datatype " + datatypeTable[dataset.datatype].name + " but expected " + datatypeTable[app_input.datatype].name + " when checking " + input);
            
            // validate dataset's datatype tags
            // NOTE: this must be a plain for-loop, not .forEach() -- `return reject(...)`
            // inside a forEach callback only exits that callback, not runApp's executor,
            // so a real tag mismatch would settle the promise as rejected (silently, since
            // -j suppresses non-JSON stdout and the CLI's caller only sees an unparseable
            // stdout) while execution fell through and still POSTed a real task to Amaretti
            // -- confirmed on the ICM cluster: repeated silent duplicate task creation on
            // every retry despite the CLI reporting failure each time. See
            // project_config_default_vs_missing memory (BrainlifeMEEG-code repo).
            let userInputTags = {};
            dataset.datatype_tags.forEach(tag => userInputTags[tag] = 1);
            for (let tag of app_input.datatype_tags) {
                if (tag.startsWith("!")) {
                    if (userInputTags[tag.substring(1)]) return reject("This app requires that the input data object for " + file_id + " should NOT have datatype tag '" + tag.substring(1) + "' but found it in " + input);
                } else {
                    if (!userInputTags[tag]) return reject("This app requires that the input data object for " + file_id + " have datatype tag '" + tag + "', but it is not set on " + input);
                }
            }
            
            inputs[file_id] = inputs[file_id] || [];
            inputs[file_id].push(dataset);
        }

        //make sure all required inputs are set
        let missing_inputs = app.inputs.filter(input=>{
            return (!input.optional && inputs[input.id] === undefined);
        });
        if(missing_inputs.length > 0) return reject("some required inputs are missing:"+missing_inputs.map(input=>input.id).toString());

        // create instance (reuse an existing one by exact id if --instance-id was given, or by
        // exact name if --instance-name was given, so multiple app runs can be grouped into a
        // single process instead of each getting its own randomly-named instance)
        let instance;
        if (opt.instanceId) {
            instance = { _id: opt.instanceId };
        } else {
            let instanceName = opt.instanceName || ((apps[0].tags||'CLI Process') + "." + (Math.random()));
            instance = await util.findOrCreateInstance(headers, instanceName, { project, desc: opt.instanceName || ("(CLI) " + app.name) });
        }
        
        // prepare config to submit the app
        let values = {};
        for (let key in app.config) {
            let appParam = app.config[key];
            let userParam = opt.config[key];
            
            if (appParam.type != 'input') {
                if(userParam === undefined) userParam = appParam.default;
                values[key] = userParam;
            }
        }
            
        //enumerate all datasets
        let dataset_ids = [];
        app.inputs.forEach(input => {
            if(inputs[input.id]) inputs[input.id].forEach(user_input=>{
                dataset_ids.push(user_input._id);
            });
        });
        dataset_ids = [...new Set(dataset_ids)]; //TODO - api does this now so I don't have to do it.

        //--direct-deps: skip /dataset/stage entirely. Every dataset already carries
        //prov.task._id/prov.subdir pointing at wherever it actually lives (a real app
        //task's own output dir, or an upload task) -- deps_config can reference that
        //directly, same as any normal chained step already does internally. This avoids
        //creating a redundant "Data Staging Task" (and its own archive read) on every
        //single step of a multi-app chain within one instance.
        if (opt.directDeps) {
            let missingProv = dataset_ids.filter(id => {
                let ds = Object.values(inputs).flat().find(d => d._id == id);
                return !(ds && ds.prov && ds.prov.task && ds.prov.subdir);
            });
            if (missingProv.length > 0) return reject("--direct-deps requires prov.task/prov.subdir on every input dataset, missing for: " + missingProv.join(", "));
        }

        await finishSubmission(opt.directDeps ? null : await stageDatasets(dataset_ids));

        function stageDatasets(dataset_ids) {
            //TODO - similar code exists on UI modals/appsubmit.vue
            return new Promise((resolve, reject2) => {
                request.post({url: config.api.warehouse+'/dataset/stage', json: true, headers,
                    body: {
                        instance_id: instance._id,
                        dataset_ids,
                    }
                }, (err, res, body)=>{
                    if(err) return reject2(err);
                    if(res.statusCode != 200) return reject2(res.body.message);
                    let task = body.task;
                    if(!opt.json) console.log("Data Staging Task Created (" + task._id + ")");
                    resolve(task);
                });
            });
        }

        async function finishSubmission(stageTask) {
            //per-dataset {taskId, subdir} to depend on: either the shared staging task
            //(dataset id used as its subdir name, as before), or -- with --direct-deps --
            //each dataset's own producing task directly.
            function taskRef(user_input) {
                if (stageTask) return { taskId: stageTask._id, subdir: user_input._id };
                return { taskId: user_input.prov.task._id, subdir: user_input.prov.subdir };
            }

            const depsByTask = {}; //taskId -> Set(subdirs), collapsed into deps_config below
            let app_inputs = [];
            app.inputs.forEach(input => {
                //find config.json key mapped to this input
                let keys = [];
                for (let key in app.config) {
                    if(app.config[key].input_id == input.id) {
                        keys.push(key);
                    }
                }

                if(inputs[input.id]) inputs[input.id].forEach(user_input=>{
                    let ref = taskRef(user_input);
                    // --direct-deps: no staging task output record to read (datatype,
                    // meta, datatype_tags, ...) from, so carry the resolved dataset's own
                    // fields over instead -- same shape the downstream code (meta merge,
                    // datatype_tags_pass) expects either way.
                    let dataset = stageTask
                        ? stageTask.config._outputs.find(output=>output.dataset_id == user_input._id)
                        : Object.assign({}, user_input, { id: ref.subdir, dataset_id: user_input._id });
                    app_inputs.push(Object.assign({}, dataset, {
                        id: input.id,
                        task_id: ref.taskId,
                        keys,
                    }));

                    depsByTask[ref.taskId] = depsByTask[ref.taskId] || new Set();
                    if(input.includes) {
                        input.includes.split("\n").forEach(include=>{
                            depsByTask[ref.taskId].add("include:"+ref.subdir+"/"+include)
                        });
                    } else {
                        depsByTask[ref.taskId].add(ref.subdir);
                    }
                });
            });
            const deps_config = Object.keys(depsByTask).map(taskId => ({
                task: taskId,
                subdirs: [...depsByTask[taskId]],
            }));

            //similar code alert
            //  ui/modal/newtask
            //  ui/modal/appsubmit
            //  bin/rule_handler
            //  cli
            const meta = {};
            app_inputs.forEach(dataset=>{
                //for(var k in dataset.meta) if(!meta[k]) meta[k] = dataset.meta[k]; //use first one
                ["subject", "session", "run"].forEach(k=>{
                    if(!meta[k]) meta[k] = dataset.meta[k]; //use first one
                });
            });
            let app_outputs = [];
            app.outputs.forEach(output=>{
                let output_req = {
                    id: output.id, 
                    datatype: output.datatype,
                    desc: output.desc||app.name, //what is this for?
                    tags: opt.tags,
                    meta,
                    archive: {
                        project: project._id,
                        desc: output.id + " from " + app.name
                    },
                };

                if(output.output_on_root) {
                    output_req.files = output.files; //optional
                } else {
                    output_req.subdir = output.id;
                }

                //handle tag pass through
                let tags = [];
                if(output.datatype_tags_pass) {
                    console.log("input for", output.datatype_tags_pass);
                    //datatype_tags_pass can reference an optional input that
                    //wasn't provided on this run (e.g. an opt01 override
                    //bundle) -- inputs[id] is then undefined, not an empty
                    //array, so guard instead of crashing.
                    (inputs[output.datatype_tags_pass] || []).forEach(dataset=>{
                        if(!dataset) return; //could this really happen?
                        if(dataset.datatype_tags) tags = tags.concat(dataset.datatype_tags);
                        Object.assign(output_req.meta, dataset.meta);
                    });
                }
                tags = tags.concat(output.datatype_tags); //add specified output tags at the end
                //a multi input can pass through the same tags from several datasets
                //(e.g. several runs all tagged eeg/egi/converted) -- dedupe rather than
                //repeating each dataset's tags once per dataset
                output_req.datatype_tags = [...new Set(tags)]
                console.dir(output_req);

                app_outputs.push(output_req);
            });

            // next sequential tid: normally read off the stage task's own _tid (the
            // server assigns that when creating the stage task in this instance). With
            // --direct-deps there's no stage task to read it from, so fetch the
            // instance's current summary directly instead.
            let nextTid;
            if (stageTask) {
                nextTid = stageTask.config._tid + 1;
            } else {
                let instanceRecord = await request({
                    url: config.api.amaretti + '/instance', headers, json: true,
                    qs: { find: JSON.stringify({ _id: instance._id }) },
                });
                let summary = (instanceRecord.instances[0].config || {}).summary || [];
                nextTid = summary.length ? Math.max(...summary.map(s=>s.tid)) + 1 : 1;
            }

            // finalize app config object
            let preparedConfig = prepareConfig(values, taskRef, inputs, datatypeTable, app);
            Object.assign(preparedConfig, {
                _app: app._id,
                _tid: nextTid,
                _inputs: app_inputs,
                _outputs: app_outputs,
            });

            ////////////////////////////////////////////////////////////////////////////////////////
            //
            // run tasks!
            //
            let submissionParams = {
                instance_id: instance._id,
                gids,
                name: app.name.trim(),
                service: app.github,
                service_branch: branch,
                config: preparedConfig,
                deps_config,
            };
            if (resource) submissionParams.preferred_resource_id = resource;
            if (opt.dryRun) {
                console.log(JSON.stringify(submissionParams, null, 2));
                return resolve({ _id: "DRYRUN", config: submissionParams.config });
            }
            request.post({ url: config.api.amaretti + "/task", headers, json: true, body: submissionParams }, (err, res, body) => {
                if (err) return reject(err);
                else if (res.statusCode != 200) return reject(res.body.message);
                if (!opt.json) console.log(app.name + " task for app '" + app.name + "' has been created.\n" +
                            "To monitor the app as it runs, please execute \nbl app wait " + body.task._id);
                resolve(body.task);
            });
        }
    });
}

function prepareConfig(values, taskRef, inputs, datatypeTable, app) {
    let idToAppInputTable = {};
    //let idToDatatype = {};
    let result = {};

    app.inputs.forEach(input => idToAppInputTable[input.id] = input);
    //app.inputs.forEach(input => idToDatatype[input.id] = input.datatype);

    Object.keys(app.config).forEach(key => {
        if (app.config[key].type == 'input') {
            const input_id = app.config[key].input_id;
            let userInputs = inputs[input_id];
            if(!userInputs) return;
            let appInput = idToAppInputTable[input_id];
            
            if (appInput.multi) {
                result[key] = result[key] || [];
                userInputs.forEach(uInput => {
                    let dtype = datatypeTable[uInput.datatype];
                    let idToFile = {};
                    dtype.files.forEach(file => idToFile[file.id] = file);
                    let inputDtypeFile = idToFile[app.config[key].file_id];
                    let ref = taskRef(uInput);
                    result[key].push("../" + ref.taskId + "/" + ref.subdir + "/" + (inputDtypeFile.filename||inputDtypeFile.dirname));
                });
            } else {
                let dtype = datatypeTable[userInputs[0].datatype];
                let idToFile = {};
                dtype.files.forEach(file => idToFile[file.id] = file);
                let inputDtypeFile = idToFile[app.config[key].file_id];
                let ref = taskRef(userInputs[0]);
                result[key] = "../" + ref.taskId + "/" + ref.subdir + "/" + (inputDtypeFile.filename||inputDtypeFile.dirname);
            }
        } else {
            result[key] = values[key];
        }
    });
    
    return result;
}

