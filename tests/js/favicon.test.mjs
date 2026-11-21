import test from "node:test";
import assert from "node:assert/strict";
import { buildFaviconSvg } from "../../diatonic/web/js/inbox.js";

test("favicon without notifications renders clean icon", () => {
  const svg0 = buildFaviconSvg(0);
  assert.ok(svg0.startsWith("<svg"));
  assert.ok(svg0.endsWith("</svg>"));
  assert.ok(!svg0.includes("fill='#ef4444'"));
  assert.ok(!svg0.includes("<text"));

  const svgNull = buildFaviconSvg(null);
  assert.equal(svgNull, svg0);
});

test("favicon with single digit unread count renders circle badge", () => {
  const svg = buildFaviconSvg(3);
  assert.ok(svg.includes("fill='#ef4444'"));
  assert.ok(svg.includes("<circle cx='23' cy='9' r='7.5'"));
  assert.ok(svg.includes(">3</text>"));
});

test("favicon with 9+ unread count renders pill badge", () => {
  const svg10 = buildFaviconSvg(10);
  assert.ok(svg10.includes("fill='#ef4444'"));
  assert.ok(svg10.includes("<rect x='13'"));
  assert.ok(svg10.includes(">9+</text>"));

  const svg99 = buildFaviconSvg(99);
  assert.ok(svg99.includes(">9+</text>"));
});
