import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  calendarBookingOwnership,
  calendarFoldSize,
  normalizeTag,
  tagStyle,
} from "../src/ui/presentation.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "src/App.jsx"), "utf8");
const calendarCss = fs.readFileSync(path.join(root, "src/styles/calendar.css"), "utf8");

function channel(hex, index) {
  return Number.parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16);
}

function luminance(hex) {
  const channels = [0, 1, 2].map((index) => channel(hex, index) / 255);
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(left, right) {
  const values = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function distance(left, right) {
  const delta = [0, 1, 2].map((index) => channel(left, index) - channel(right, index));
  return Math.hypot(...delta);
}

test("calendar ownership comes from ownerId and does not treat every admin booking as mine", () => {
  assert.equal(calendarBookingOwnership({ ownerId: "user-a" }, "user-a"), "mine");
  assert.equal(calendarBookingOwnership({ ownerId: "user-b" }, "user-a"), "other");
  assert.equal(calendarBookingOwnership({ ownerId: "" }, "user-a"), "unknown");
  assert.equal(calendarBookingOwnership({ ownerId: "user-a" }, ""), "unknown");
  assert.equal(calendarBookingOwnership(null, "user-a"), "unknown");
});

test("the sticky fold is 12px on ordinary cards and 8px on short cards", () => {
  assert.equal(calendarFoldSize({ durationMinutes: 60, slotMinutes: 30 }), 12);
  assert.equal(calendarFoldSize({ durationMinutes: 90, slotMinutes: 30 }), 12);
  assert.equal(calendarFoldSize({ durationMinutes: 30, slotMinutes: 30 }), 8);
  assert.equal(calendarFoldSize({ durationMinutes: 60, slotMinutes: 60 }), 8);
  assert.equal(calendarFoldSize({ durationMinutes: 15, slotMinutes: 30 }), 8);
});

test("each tag slot has a fold darker than its card tint, including sage and slate", () => {
  const folds = [1, 2, 3, 4].map((slot) => normalizeTag({ slot }));
  const surfaces = new Set(folds.map((tag) => tag.surface));
  assert.equal(surfaces.size, 4);
  for (const tag of folds) {
    assert.equal(tagStyle(tag)["--tag-fold"], tag.fold);
    assert.ok(luminance(tag.fold) < luminance(tag.surface) - 0.015, tag.fold);
    assert.ok(distance(tag.fold, tag.surface) >= 36, `${tag.slot} ${tag.fold}`);
    assert.notEqual(tag.fold, tag.color);
  }
  assert.ok(distance(folds[2].fold, folds[2].surface) >= 40);
  assert.ok(distance(folds[3].fold, folds[3].surface) >= 40);
});

test("other-booking text stays readable on the flat ivory fill", () => {
  assert.ok(contrastRatio("#66655f", "#f3efe8") >= 4.5);
  assert.ok(contrastRatio("#6d6c66", "#f3efe8") >= 4.5);
  assert.ok(contrastRatio("#66655f", "#ebe6dc") >= 4.5);
});

test("the calendar marks mine and others in both class and accessible name", () => {
  assert.match(appSource, /calendarBookingOwnership\(booking, currentUser\.id\)/);
  assert.match(appSource, /ownership === "mine" \? "booking-mine" : "booking-other"/);
  assert.match(appSource, /ownership === "mine" \? "我的预约" : ownership === "other" \? "他人预约"/);
  assert.match(appSource, /data-booking-ownership=\{ownership\}/);
  assert.match(appSource, /className="booking-fold"/);
  assert.match(appSource, /ownership === "mine" && <i \/>/);
  assert.doesNotMatch(appSource, /booking-fold[\s\S]{0,80}booking-other/);
});

test("mine cards clip a fold and others stay flat, with motion gated", () => {
  assert.match(calendarCss, /\.booked-slot\.booking-other \{[\s\S]*?background: #f3efe8;[\s\S]*?box-shadow: none;/);
  assert.match(calendarCss, /\.booked-slot\.booking-mine \{[\s\S]*?--fold-size: 12px;/);
  assert.match(calendarCss, /\.booked-slot\.booking-mine\.booking-short \{[\s\S]*?--fold-size: 8px;/);
  assert.match(calendarCss, /mask-image: linear-gradient\(135deg, transparent var\(--fold-cut\), #000 var\(--fold-cut\)\)/);
  assert.match(calendarCss, /\.booking-card \{[\s\S]*?drop-shadow\(0 3px 7px rgba\(20, 20, 19, 0\.04\)\)/);
  assert.match(calendarCss, /\.booking-fold \{[\s\S]*?var\(--tag-fold, var\(--tag-line\)\)/);
  assert.match(calendarCss, /@media \(prefers-reduced-motion: no-preference\) \{[\s\S]*?\.booked-slot\.booking-mine:hover \{[\s\S]*?transform: translateY\(-1px\);/);
  assert.match(calendarCss, /\.available-slot\.past-slot,[\s\S]*?\.available-slot\.outside-work-slot \{[\s\S]*?background: #ebe6dc;/);
  assert.match(calendarCss, /\.available-slot\.past-slot \.slot-affordance,[\s\S]*?opacity: 0;/);
  assert.match(calendarCss, /\.available-slot\.past-slot:hover \.slot-affordance,[\s\S]*?opacity: 1;/);
  assert.doesNotMatch(calendarCss, /url\(["']?[^"')]*paper/);
});
