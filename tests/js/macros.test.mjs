import test from "node:test";
import assert from "node:assert/strict";

import { testCondition, runMacro, startRecording, stopRecording, hookRecorder } from "../../diatonic/web/js/macros.js";
import { register, run } from "../../diatonic/web/js/commands.js";

test("testCondition evaluates conditional expressions", () => {
  const flags = new Set(["idle", "panel:chat", "mac"]);

  assert.equal(testCondition("", flags), true);
  assert.equal(testCondition("idle", flags), true);
  assert.equal(testCondition("!input", flags), true);
  assert.equal(testCondition("input", flags), false);
  assert.equal(testCondition("panel:chat && idle", flags), true);
  assert.equal(testCondition("panel:files || panel:chat", flags), true);
  assert.equal(testCondition("panel:files && idle", flags), false);
  assert.equal(testCondition("(panel:files || panel:chat) && !input", flags), true);
});

test("runMacro executes steps in order and respects if conditions", async () => {
  const log = [];
  register({ id: "test.cmd1", run: (a) => { log.push(["cmd1", a]); } });
  register({ id: "test.cmd2", run: (a) => { log.push(["cmd2", a]); } });
  register({ id: "test.cmd3", run: (a) => { log.push(["cmd3", a]); } });

  const flags = new Set(["feature_enabled"]);

  const macro = {
    id: "test_macro",
    name: "Test Macro",
    steps: [
      { command: "test.cmd1", args: { x: 1 } },
      { wait: 20 },
      { if: "!feature_enabled", command: "test.cmd2", args: { x: 2 } },
      { if: "feature_enabled", command: "test.cmd3", args: { x: 3 } },
    ],
  };

  const ok = await runMacro(macro, { flagsProvider: () => flags });
  assert.equal(ok, true);
  assert.deepEqual(log, [
    ["cmd1", { x: 1 }],
    ["cmd3", { x: 3 }],
  ]);
});

test("macro recorder captures executed commands", async () => {
  hookRecorder();
  const unregA = register({ id: "test.recA", run: () => "A" });
  const unregB = register({ id: "test.recB", run: (a) => a });
  const unregIgnored = register({ id: "macro.record.test", run: () => "ignore" });

  startRecording("My Rec");
  await run("test.recA");
  await run("test.recB", { foo: "bar" });
  await run("macro.record.test");

  const rec = stopRecording();
  assert.equal(rec.name, "My Rec");
  assert.deepEqual(rec.steps, [
    { command: "test.recA" },
    { command: "test.recB", args: { foo: "bar" } },
  ]);

  unregA();
  unregB();
  unregIgnored();
});
