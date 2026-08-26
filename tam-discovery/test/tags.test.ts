import { describe, expect, it } from "vitest";
import { normalizeTag } from "../src/worker/lib/tags";

describe("normalizeTag", () => {
  it.each([
    ["After Hours", "after-hours"],
    ["after-hours", "after-hours"],
    ["after_hours", "after-hours"],
    ["  Q3  ", "q3"],
    ["Dashboard", "dashboard"],
    ["dashboard ", "dashboard"],
    ["a--b__c  d", "a-b-c-d"],
    ["---", ""],
    ["", ""],
  ])("normalizes %j to %j", (input, expected) => {
    expect(normalizeTag(input)).toBe(expected);
  });
});
