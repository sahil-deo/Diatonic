# Making Diatonic hackable: bottlenecks and plan

Diagnosis from reading the code (not from running models). The question: are the models too weak to build apps, or do they lack the tools to build usable ones? Mostly the tools, with model size as a second factor.

## What the code shows

Each time a model failed to build something, the fix was a narrower mechanism:

1. `add_panel` takes free-form JS, but is labelled "only for something create_app can't do".
2. **Data apps** (`create_app`, `add_list`): lists and trackers, no code.
3. **API apps** (`connect_api`, `add_action`): web APIs, with a test call after every step.
4. **Tool apps** (`toolapps.py`): wrap existing tools. `plan()` uses regexes on the user's sentence (`VERBS`, `ABOUT_APP`) to guess what the app should do.

So the harness works around the model's limits more than it lets the model build. Apps work when they are data the model fills in, and fail when the model has to write code.

## Tool bottlenecks

1. **No feedback loop for code.** `add_panel` and `add_script` take a string, save it and show an approval card. Nothing renders the panel, captures console errors, or returns a screenshot or DOM to the model. (`todo.md`: "a broken script is only noticed after it runs.") API apps work because `add_action` test-calls immediately and returns the result. Models can't write UI code blind.
2. **Code is written in a single tool argument.** Up to 20k characters of JSON-escaped JS in one call: no file, no patch/edit, no multiple files, no "change the button colour" without regenerating everything. A 4B model is at its worst at long escaped strings in structured output.
3. **The SDK is bespoke and undocumented to the model.** `diatonic.h`, `el` and the `diatonic` object are in no training set. The model gets one sentence of description, no reference, no templates, no example apps.
4. **Custom panels are cut off from the harness.** A panel can use `localStorage`, but the agent can't see its data, call app tools or the scheduler, or test it. Data apps get generated tools; panels get none.
5. **Approval gates the whole blob.** The user reviews a wall of JS. A good guardrail, but with no preview or test every failed attempt is expensive.
6. **Context is tight.** `num_ctx` defaults to 8192; tool offering is keyword-routed and trimmed to fit. The model may not be offered the right tools, and the code it writes competes with the tool catalog and history for space.

## Model bottleneck

`qwen3:4b-instruct` is fine at picking a tool and filling arguments (the harness repairs the rest). It is not good at writing ~200 lines of working DOM code zero-shot, and can't debug code it can't see run. Better tools help, but it won't be a reliable app author. `todo.md` already has the right idea under "model escalation": a bigger model builds, the 4B runs.

## Diagnostic experiment (do first)

Run the same build request (e.g. a Pomodoro timer, a unit converter) with Gemini or an 8B+ model selected in the status bar.

- Fails the same way: tools are the bottleneck (feedback loop, SDK).
- Succeeds: the 4B is the problem; fix is escalation for build requests.

Expected: a mix. A strong model gets further but still ships broken panels nobody notices until the user opens them.



## Open question

Which app failed when building was attempted? That tells us whether it was a code failure (broken JS), a routing failure (wrong tools offered), or a scope failure (needed something panels can't do).
