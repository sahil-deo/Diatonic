// Pure data-model functions for the panel workspace layout tree.
// Node types:
//   split: { type: "split", dir: "row" | "col", sizes: number[], children: Node[] }
//   stack: { type: "stack", id: string, active: number, tabs: Tab[] }
// Tab:
//   { id: string, app: string, params: object }

export const MIN_SIZE = 0.05;

let idSeq = 0;
export const nextId = (prefix = "p") => `${prefix}_${Date.now().toString(36)}_${(++idSeq).toString(36)}`;

export function clone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

export const PRESETS = ["single", "side-by-side", "stacked", "three-column", "grid"];

/** Default presets: "current" (chat on the left, app panel on the right), "single", "side-by-side", "stacked", "three-column", "grid". */
export function fromPreset(name = "current", rightApp = "files") {
  const norm = String(name || "current").toLowerCase().trim();
  const chatStack = {
    type: "stack",
    id: nextId("s"),
    active: 0,
    tabs: [{ id: nextId("t"), app: "chat", params: { conversationId: null } }],
  };

  if (norm === "single") {
    return validate({ v: 1, root: chatStack });
  }

  const rightStack = {
    type: "stack",
    id: nextId("s"),
    active: 0,
    tabs: [{ id: nextId("t"), app: rightApp || "files", params: {} }],
  };

  if (norm === "stacked") {
    return validate({
      v: 1,
      root: {
        type: "split",
        dir: "col",
        sizes: [0.5, 0.5],
        children: [chatStack, rightStack],
      },
    });
  }

  if (norm === "three-column") {
    const thirdStack = {
      type: "stack",
      id: nextId("s"),
      active: 0,
      tabs: [{ id: nextId("t"), app: "shell", params: {} }],
    };
    return validate({
      v: 1,
      root: {
        type: "split",
        dir: "row",
        sizes: [0.33, 0.34, 0.33],
        children: [chatStack, rightStack, thirdStack],
      },
    });
  }

  if (norm === "grid") {
    const bottomChat = {
      type: "stack",
      id: nextId("s"),
      active: 0,
      tabs: [{ id: nextId("t"), app: "shell", params: {} }],
    };
    const bottomApp = {
      type: "stack",
      id: nextId("s"),
      active: 0,
      tabs: [{ id: nextId("t"), app: "notes", params: {} }],
    };
    const leftCol = {
      type: "split",
      dir: "col",
      sizes: [0.5, 0.5],
      children: [chatStack, bottomChat],
    };
    const rightCol = {
      type: "split",
      dir: "col",
      sizes: [0.5, 0.5],
      children: [rightStack, bottomApp],
    };
    return validate({
      v: 1,
      root: {
        type: "split",
        dir: "row",
        sizes: [0.5, 0.5],
        children: [leftCol, rightCol],
      },
    });
  }

  // "side-by-side" or "current"
  return validate({
    v: 1,
    root: {
      type: "split",
      dir: "row",
      sizes: [0.6, 0.4],
      children: [chatStack, rightStack],
    },
  });
}

/** Ensures valid tree invariants: unique IDs, non-empty stacks, collapses single-child splits, normalised sizes. */
export function validate(tree) {
  if (!tree || typeof tree !== "object") return fromPreset("single");
  const t = clone(tree);
  t.v = 1;
  const seenIds = new Set();

  function fixNode(node) {
    if (!node || typeof node !== "object") return null;

    if (node.type === "stack") {
      if (!node.id || seenIds.has(node.id)) node.id = nextId("s");
      seenIds.add(node.id);

      const validTabs = [];
      for (const tab of node.tabs || []) {
        if (!tab || typeof tab !== "object" || !tab.app) continue;
        if (!tab.id || seenIds.has(tab.id)) tab.id = nextId("t");
        seenIds.add(tab.id);
        tab.params = tab.params || {};
        validTabs.push(tab);
      }
      if (!validTabs.length) return null; // remove empty stack

      node.tabs = validTabs;
      node.active = Math.max(0, Math.min(validTabs.length - 1, Number(node.active) || 0));
      return node;
    }

    if (node.type === "split") {
      node.dir = node.dir === "col" ? "col" : "row";
      const validChildren = [];
      const origSizes = Array.isArray(node.sizes) ? node.sizes : [];

      for (const child of node.children || []) {
        const fixed = fixNode(child);
        if (fixed) validChildren.push(fixed);
      }

      if (!validChildren.length) return null;
      if (validChildren.length === 1) return validChildren[0]; // collapse single-child split

      node.children = validChildren;

      // Normalise sizes
      let sizes = origSizes.slice(0, validChildren.length);
      while (sizes.length < validChildren.length) sizes.push(1 / validChildren.length);
      let sum = sizes.reduce((a, b) => a + (Number(b) || 0), 0) || 1;
      sizes = sizes.map((s) => Math.max(MIN_SIZE, (Number(s) || MIN_SIZE) / sum));
      sum = sizes.reduce((a, b) => a + b, 0);
      node.sizes = sizes.map((s) => s / sum);
      return node;
    }

    return null;
  }

  t.root = fixNode(t.root);
  if (!t.root) return { v: 1, root: null };
  return t;
}

export function allTabs(tree) {
  const tabs = [];
  function walk(node) {
    if (!node) return;
    if (node.type === "stack") {
      for (const t of node.tabs || []) tabs.push({ ...t, stackId: node.id });
    } else if (node.type === "split") {
      for (const c of node.children || []) walk(c);
    }
  }
  walk(tree?.root);
  return tabs;
}

export function findTab(tree, tabId) {
  let result = null;
  function walk(node, parent = null) {
    if (!node || result) return;
    if (node.type === "stack") {
      const idx = node.tabs.findIndex((t) => t.id === tabId);
      if (idx !== -1) result = { tab: node.tabs[idx], index: idx, stack: node, parent };
    } else if (node.type === "split") {
      for (const c of node.children || []) walk(c, node);
    }
  }
  walk(tree?.root);
  return result;
}

export function findStack(tree, stackId) {
  let result = null;
  function walk(node, parent = null) {
    if (!node || result) return;
    if (node.type === "stack") {
      if (node.id === stackId) result = { stack: node, parent };
    } else if (node.type === "split") {
      for (const c of node.children || []) walk(c, node);
    }
  }
  walk(tree?.root);
  return result;
}

/** Open a panel tab into a stack (or create/reuse stack). */
export function openPanel(tree, tabSpec, targetStackId = null) {
  const t = clone(tree);
  const newTab = {
    id: tabSpec.id || nextId("t"),
    app: tabSpec.app,
    params: tabSpec.params || {},
  };

  let target = targetStackId ? findStack(t, targetStackId)?.stack : null;
  if (!target) {
    // Default to the rightmost stack, or the only stack
    const allStacks = [];
    function walk(n) {
      if (!n) return;
      if (n.type === "stack") allStacks.push(n);
      else if (n.type === "split") (n.children || []).forEach(walk);
    }
    walk(t?.root);
    target = allStacks[allStacks.length - 1];
  }

  if (target) {
    target.tabs.push(newTab);
    target.active = target.tabs.length - 1;
  } else {
    t.root = {
      type: "stack",
      id: nextId("s"),
      active: 0,
      tabs: [newTab],
    };
  }
  return validate(t);
}

/** Close a panel tab by tab ID. */
export function closePanel(tree, tabId) {
  const t = clone(tree);
  const found = findTab(t, tabId);
  if (!found) return t;

  const { stack, index } = found;
  stack.tabs.splice(index, 1);
  if (stack.active >= stack.tabs.length) {
    stack.active = Math.max(0, stack.tabs.length - 1);
  }
  return validate(t);
}

/** Split an existing panel or stack into two adjacent panels. */
export function split(tree, panelId, dir = "row", newTabSpec) {
  if (!newTabSpec || !newTabSpec.app || !newTabSpec.app.trim()) return tree;
  const t = clone(tree);
  const found = findTab(t, panelId);
  if (!found) return t;

  const stack = found.stack;
  const newTab = {
    id: newTabSpec.id || nextId("t"),
    app: newTabSpec.app.trim(),
    params: newTabSpec.params || {},
  };
  const newStack = {
    type: "stack",
    id: nextId("s"),
    active: 0,
    tabs: [newTab],
  };

  const oldStackCopy = clone(stack);
  // Replace current stack contents with a split containing oldStack and newStack
  Object.keys(stack).forEach((k) => delete stack[k]);
  stack.type = "split";
  stack.dir = dir;
  stack.sizes = [0.5, 0.5];
  stack.children = [oldStackCopy, newStack];

  return validate(t);
}

/** Move tab from one stack to another. */
export function moveTab(tree, tabId, targetStackId, targetIndex = -1) {
  const t = clone(tree);
  const foundTab = findTab(t, tabId);
  const foundStack = findStack(t, targetStackId);
  if (!foundTab || !foundStack) return t;

  const { stack: srcStack, index: srcIdx, tab } = foundTab;
  srcStack.tabs.splice(srcIdx, 1);
  if (srcStack.active >= srcStack.tabs.length) {
    srcStack.active = Math.max(0, srcStack.tabs.length - 1);
  }

  const destStack = foundStack.stack;
  const insertAt = targetIndex < 0 || targetIndex > destStack.tabs.length ? destStack.tabs.length : targetIndex;
  destStack.tabs.splice(insertAt, 0, tab);
  destStack.active = insertAt;

  return validate(t);
}

/** Swap two tabs in the tree. */
export function swapPanels(tree, tabId1, tabId2) {
  const t = clone(tree);
  const f1 = findTab(t, tabId1);
  const f2 = findTab(t, tabId2);
  if (!f1 || !f2) return t;

  const tab1 = f1.stack.tabs[f1.index];
  const tab2 = f2.stack.tabs[f2.index];

  f1.stack.tabs[f1.index] = tab2;
  f2.stack.tabs[f2.index] = tab1;

  return validate(t);
}

/** Resize split children at path or node. */
export function resize(tree, splitNodeId, newSizes) {
  const t = clone(tree);
  function walk(n) {
    if (!n) return;
    if (n.type === "split") {
      if (!splitNodeId || n.id === splitNodeId) {
        n.sizes = newSizes;
        return;
      }
      n.children.forEach(walk);
    }
  }
  walk(t.root);
  return validate(t);
}

/** Find spatial neighbor tab given direction ("left", "right", "up", "down"). */
export function focusNeighbor(tree, currentTabId, direction) {
  const tabs = allTabs(tree);
  if (tabs.length <= 1) return null;
  const curIdx = tabs.findIndex((t) => t.id === currentTabId);
  if (curIdx === -1) return null;

  // Linear / topological order fallback for row/col splits
  if (direction === "right" || direction === "down") {
    return tabs[(curIdx + 1) % tabs.length].id;
  }
  if (direction === "left" || direction === "up") {
    return tabs[(curIdx - 1 + tabs.length) % tabs.length].id;
  }
  return null;
}
