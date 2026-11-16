import test from "node:test";
import assert from "node:assert/strict";

import {
  fromPreset,
  validate,
  openPanel,
  closePanel,
  split,
  moveTab,
  swapPanels,
  resize,
  focusNeighbor,
  allTabs,
  findTab,
} from "../../diatonic/web/js/layout.js";

test("fromPreset creates valid trees", () => {
  const current = fromPreset("current");
  assert.equal(current.v, 1);
  assert.equal(current.root.type, "split");
  assert.equal(current.root.dir, "row");
  assert.equal(current.root.children.length, 2);
  assert.equal(current.root.children[0].type, "stack");
  assert.equal(current.root.children[0].tabs[0].app, "chat");
  assert.equal(current.root.children[1].tabs[0].app, "files");

  const single = fromPreset("single");
  assert.equal(single.root.type, "stack");
  assert.equal(single.root.tabs[0].app, "chat");

  const stacked = fromPreset("stacked");
  assert.equal(stacked.root.type, "split");
  assert.equal(stacked.root.dir, "col");
  assert.equal(stacked.root.children.length, 2);

  const threeCol = fromPreset("three-column");
  assert.equal(threeCol.root.type, "split");
  assert.equal(threeCol.root.dir, "row");
  assert.equal(threeCol.root.children.length, 3);

  const grid = fromPreset("grid");
  assert.equal(grid.root.type, "split");
  assert.equal(grid.root.dir, "row");
  assert.equal(grid.root.children.length, 2);
  assert.equal(grid.root.children[0].type, "split");
  assert.equal(grid.root.children[0].dir, "col");
});

test("validate repairs invalid sizes, collapses single-child splits, and removes empty stacks", () => {
  const bad = {
    root: {
      type: "split",
      dir: "row",
      sizes: [10, 0], // bad sizes
      children: [
        { type: "stack", id: "s1", active: 5, tabs: [{ id: "t1", app: "chat" }] },
        { type: "stack", id: "s2", active: 0, tabs: [] }, // empty stack
      ],
    },
  };
  const fixed = validate(bad);
  // Empty stack s2 removed, split with single child s1 collapsed into s1
  assert.equal(fixed.root.type, "stack");
  assert.equal(fixed.root.id, "s1");
  assert.equal(fixed.root.tabs.length, 1);
  assert.equal(fixed.root.active, 0); // clamped from 5 to 0
});

test("openPanel adds a tab and makes it active", () => {
  let tree = fromPreset("single");
  assert.equal(allTabs(tree).length, 1);

  tree = openPanel(tree, { app: "todos" });
  const tabs = allTabs(tree);
  assert.equal(tabs.length, 2);
  assert.equal(tabs[1].app, "todos");
  assert.equal(tree.root.active, 1);
});

test("closePanel removes tab, collapses empty stack, and shrinks tree", () => {
  let tree = fromPreset("current");
  const tabs = allTabs(tree);
  assert.equal(tabs.length, 2);

  // Close the right tab (files)
  tree = closePanel(tree, tabs[1].id);
  assert.equal(allTabs(tree).length, 1);
  // Tree should have collapsed from split into a single stack
  assert.equal(tree.root.type, "stack");
  assert.equal(tree.root.tabs[0].app, "chat");
});

test("split turns a stack into a split node with two stacks", () => {
  let tree = fromPreset("single");
  const tabId = allTabs(tree)[0].id;

  tree = split(tree, tabId, "col", { app: "notes" });
  assert.equal(tree.root.type, "split");
  assert.equal(tree.root.dir, "col");
  assert.equal(tree.root.children.length, 2);
  assert.equal(tree.root.children[0].tabs[0].app, "chat");
  assert.equal(tree.root.children[1].tabs[0].app, "notes");
  assert.deepEqual(tree.root.sizes, [0.5, 0.5]);
});

test("moveTab moves tab between stacks", () => {
  let tree = fromPreset("current");
  const tabs = allTabs(tree);
  const chatTab = tabs[0];
  const filesTab = tabs[1];

  // Move files tab into chat's stack
  tree = moveTab(tree, filesTab.id, chatTab.stackId, 0);
  assert.equal(tree.root.type, "stack"); // second stack became empty and split collapsed
  assert.equal(tree.root.tabs.length, 2);
  assert.equal(tree.root.tabs[0].id, filesTab.id);
  assert.equal(tree.root.tabs[1].id, chatTab.id);
});

test("swapPanels swaps two tabs", () => {
  let tree = fromPreset("current");
  const tabs = allTabs(tree);
  tree = swapPanels(tree, tabs[0].id, tabs[1].id);

  const newTabs = allTabs(tree);
  assert.equal(newTabs[0].app, "files");
  assert.equal(newTabs[1].app, "chat");
});

test("resize normalises sizes to sum to 1", () => {
  let tree = fromPreset("current");
  tree = resize(tree, null, [0.8, 0.2]);
  assert.equal(Math.round(tree.root.sizes[0] * 10), 8);
  assert.equal(Math.round(tree.root.sizes[1] * 10), 2);
});

test("focusNeighbor navigates to next and prev tab", () => {
  let tree = fromPreset("current");
  const tabs = allTabs(tree);
  const next = focusNeighbor(tree, tabs[0].id, "right");
  assert.equal(next, tabs[1].id);

  const prev = focusNeighbor(tree, tabs[1].id, "left");
  assert.equal(prev, tabs[0].id);
});

test("openPanel on empty tree creates a stack with the opened app", () => {
  let empty = { v: 1, root: null };
  let tree = openPanel(empty, { app: "files" });
  assert.equal(tree.root.type, "stack");
  assert.equal(tree.root.tabs.length, 1);
  assert.equal(tree.root.tabs[0].app, "files");

  // Close the only tab
  let closed = closePanel(tree, tree.root.tabs[0].id);
  assert.equal(closed.root, null);

  // Re-open another panel from empty
  let reopened = openPanel(closed, { app: "todos" });
  assert.equal(reopened.root.type, "stack");
  assert.equal(reopened.root.tabs.length, 1);
  assert.equal(reopened.root.tabs[0].app, "todos");
});

test("closed tab history captures app and restores into tree", () => {
  let tree = fromPreset("current");
  const tabs = allTabs(tree);
  const closing = tabs.find((t) => t.app === "files");
  const history = [];
  history.push({ app: closing.app, params: closing.params || {}, stackId: closing.stackId });

  tree = closePanel(tree, closing.id);
  assert.equal(allTabs(tree).some((t) => t.app === "files"), false);

  const entry = history.pop();
  tree = openPanel(tree, { app: entry.app, params: entry.params }, entry.stackId);
  assert.equal(allTabs(tree).some((t) => t.app === "files"), true);
});
