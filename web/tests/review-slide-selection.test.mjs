import test from "node:test";
import assert from "node:assert/strict";
import { selectReferenceGroup } from "../src/app/components/admin/surmons/review-slide-selection.ts";

const groups = [
  {
    id: "opening",
    before_paragraph_id: null,
    hidden: true,
    needs_review: true,
  },
  {
    id: "jeremiah-first",
    before_paragraph_id: "8",
    hidden: false,
    needs_review: true,
  },
  {
    id: "deuteronomy-47",
    before_paragraph_id: "8",
    hidden: false,
    needs_review: false,
  },
  {
    id: "jeremiah-return",
    before_paragraph_id: "10",
    hidden: false,
    needs_review: true,
  },
  {
    id: "deuteronomy-28",
    before_paragraph_id: "10",
    hidden: false,
    needs_review: false,
  },
];

test("a transient Jeremiah frame does not displace the stable slide at either occurrence", () => {
  assert.equal(selectReferenceGroup(groups, "8")?.id, "deuteronomy-47");
  assert.equal(selectReferenceGroup(groups, "10")?.id, "deuteronomy-28");
});

test("manual Jeremiah inspection does not follow navigation to another paragraph", () => {
  const inspection = { paragraphId: "8", groupId: "jeremiah-first" };
  assert.equal(
    selectReferenceGroup(groups, "8", inspection)?.id,
    "jeremiah-first",
  );
  assert.equal(selectReferenceGroup(groups, "9", inspection), undefined);
  assert.equal(
    selectReferenceGroup(groups, "10", inspection)?.id,
    "deuteronomy-28",
  );
});

test("no assigned stable slide leaves the reference area empty", () => {
  assert.equal(selectReferenceGroup(groups, "9"), undefined);
  assert.equal(selectReferenceGroup(groups.slice(0, 2), "8"), undefined);
});

test("hidden and uncertain source occurrences remain explicitly inspectable", () => {
  assert.equal(
    selectReferenceGroup(groups, "9", { paragraphId: "9", groupId: "opening" })
      ?.id,
    "opening",
  );
  assert.equal(
    selectReferenceGroup(groups, "8", { paragraphId: "8", groupId: "missing" })
      ?.id,
    "deuteronomy-47",
  );
});
