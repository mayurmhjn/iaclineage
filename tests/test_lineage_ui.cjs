const {test} = require("node:test");
const assert = require("node:assert/strict");
const {filterFieldTree, filterTerraformCache, createGraph, fieldMatches, relationshipMeta, groupLineage} = require("../src/iaclineage/ui/report.js");

const node = id => ({id});
const edge = (id, source, sourceField, target, targetField, extra = {}) => ({
  id, source, sourceField, target, targetField,
  resolution: "resolved", certainty: "direct", kind: "reference", ...extra,
});
const ids = result => new Set(result.endpoints.map(endpoint => endpoint.id));

test("provisioner search preserves children and block traces exclude sibling blocks", () => {
  const fields = [0, 1].map(i => ({path: `provisioner[${i}]`, kind: "block", provisionerType: i ? "file" : "remote-exec",
    children: [{path: `provisioner[${i}].source`, children: []}]}));
  assert.deepEqual(filterFieldTree(fields, "REMOTE-EXEC"), [fields[0]]);
  const graph = createGraph({nodes: ["resource", "a", "b"].map(node), edges: [
    edge(0, "resource", "provisioner[0].inline[0]", "a", "value"),
    edge(1, "resource", "provisioner[1].source", "b", "value"),
  ]});
  assert.deepEqual(ids(graph.trace("resource", "provisioner[0]", "up", Infinity)), new Set(["resource", "a"]));
});

test("conditional roles distinguish control from possible values and preserve trace stops", () => {
  const roles = role => [{conditional: "main.tf:1:1-1:70", role}];
  const edges = [
    edge(0, "result", "value", "flag", "value", {certainty: "expression", conditionalRoles: roles("condition")}),
    edge(1, "result", "value", "a", "value", {certainty: "expression", conditionalRoles: roles("true")}),
    edge(2, "result", "value", "flag", "value", {certainty: "expression", conditionalRoles: roles("false")}),
    edge(3, "flag", "value", "leaf", "value"),
  ];
  const graph = createGraph({nodes: ["result", "flag", "a", "leaf"].map(node), edges});
  assert.equal(relationshipMeta(edges[0], graph.nodes).label, "Condition reference");
  assert.equal(relationshipMeta(edges[1], graph.nodes).label, "Possible value reference");
  const nested = {...edges[1], conditionalRoles: [...roles("condition"), ...roles("true")]};
  assert.equal(relationshipMeta(nested, graph.nodes).label, "Condition reference");
  const trace = graph.trace("result", "value", "up", Infinity);
  assert.equal(trace.connections.length, 3);
  assert.ok(!ids(trace).has("leaf"));
  assert.ok(trace.branches.filter(b => b.endpoint !== '["result","value"]').every(b => b.reason === "Expression boundary"));
  const downstream = graph.trace("a", "value", "down", Infinity);
  assert.equal(downstream.branches.find(b => b.endpoint.includes("result")).reason, "Expression boundary");
});

test("grouped declarations retain distinct field ports and branch identity", () => {
  const graph = createGraph({nodes: ["root", "shared", "leaf"].map(node), edges: [
    edge(10, "root", "alias.name", "shared", "domain_name"),
    edge(11, "root", "alias.zone", "shared", "zone_id"),
    edge(12, "shared", "domain_name", "leaf", "value"),
  ]});
  const result = graph.trace("root", "", "up", 1, 80, new Map());
  const grouped = groupLineage(result);
  assert.equal(grouped.groups.length, 2);
  const shared = grouped.groups.find(group => group.id === "shared");
  assert.deepEqual([...shared.fields.keys()], ["domain_name", "zone_id"]);
  assert.deepEqual([...grouped.groups.find(group => group.id === "root").fields.keys()], ["alias.name", "alias.zone"]);
  const branch = result.branches.find(branch => branch.endpoint.includes('domain_name') && branch.expandable);
  const expanded = graph.trace("root", "", "up", 1, 80, new Map([[branch.key, true]]));
  assert.equal(groupLineage(expanded).groups.length, 3);
  assert.equal(grouped.ports.get(result.connections[0]).supplier.row.edges.has(10), true);
  assert.equal(shared.fields.get("zone_id").edges.has(10), false);
});

test("direct arrivals extend shared endpoints regardless of expression order or direction", () => {
  for (const direction of ["up", "down"]) {
    for (const delayed of [false, true]) {
      const edges = [
        edge(0, "root", "value", "shared", "value", {certainty: "expression"}),
        edge(1, "root", "value", delayed ? "bridge" : "shared", "value"),
        edge(2, "shared", "value", "leaf", "value"),
        ...(delayed ? [edge(3, "bridge", "value", "shared", "value")] : []),
      ];
      if (direction === "down") {
        for (const item of edges) [item.source, item.target] = [item.target, item.source];
      }
      for (const ordered of [edges, edges.toReversed()]) {
        const graph = createGraph({nodes: ["root", "shared", "leaf", "bridge"].map(node), edges: ordered});
        const result = graph.trace("root", "value", direction, Infinity);
        assert.ok(ids(result).has("leaf"));
        assert.equal(result.connections.length, edges.length);
        assert.equal(result.endpoints.find(item => item.id === "shared").blockedReason, "");
        assert.equal(Math.abs(result.endpoints.find(item => item.id === "shared").layer), delayed ? 2 : 1);
      }
    }
  }
});

test("grouping preserves carried object members, isolated endpoints and cycles", () => {
  const graph = createGraph({nodes: ["root", "input"].map(node), edges: [
    edge(0, "root", "settings", "input", "value"),
    edge(1, "input", "value", "root", "settings"),
  ]});
  const result = graph.trace("root", "settings.name", "up", Infinity);
  const grouped = groupLineage(result);
  assert.equal(grouped.ports.size, result.connections.length);
  assert.deepEqual([...grouped.groups.find(group => group.id === "input").fields.keys()], ["value.name"]);
  const empty = groupLineage(createGraph({nodes:[node("alone")], edges:[]}).trace("alone"));
  assert.equal(empty.groups[0].fields.size, 1);
});

test("field matching respects segments and container fields", () => {
  assert.equal(fieldMatches("name", "namespace"), false);
  assert.equal(fieldMatches("tags", "tags.Name"), true);
  assert.equal(fieldMatches("ingress[0]", "ingress[1].port"), false);
  assert.equal(fieldMatches("", "anything"), true);
});

test("upstream follows field-specific module input/output chains without unrelated arguments", () => {
  const graph = createGraph({
    nodes: ["web", "compute.input", "compute", "network.output", "subnet", "other"].map(node),
    edges: [
      edge(0, "web", "subnet_id", "compute.input", "value"),
      edge(1, "compute.input", "value", "compute", "subnet_id", {kind: "module-input"}),
      edge(2, "compute", "subnet_id", "network.output", "value"),
      edge(3, "network.output", "value", "subnet", "id"),
      edge(4, "compute", "ami", "other", "value"),
      edge(5, "subnet", "cidr_block", "other", "value"),
    ],
  });
  const upstream = graph.trace("web", "subnet_id", "up", Infinity);
  assert.deepEqual(ids(upstream), new Set(["web", "compute.input", "compute", "network.output", "subnet"]));
  assert.equal(upstream.connections.length, 4);
  const downstream = graph.trace("subnet", "id", "down", Infinity);
  assert.equal(ids(downstream).has("web"), true);
  assert.equal(ids(downstream).has("other"), false);
});

test("unknown targets render as endpoints; expressions stop after showing evidence", () => {
  const graph = createGraph({nodes: ["a", "b", "c"].map(node), edges: [
    edge(0, "a", "value", "b", "value", {certainty: "expression"}),
    edge(1, "b", "value", "c", "value"),
    edge(2, "a", "other", "missing", "id", {resolution: "unresolved"}),
  ]});
  assert.deepEqual(ids(graph.trace("a", "", "up", Infinity)), new Set(["a", "b", "missing"]));
});

test("member selection survives whole-object module bindings", () => {
  const graph = createGraph({nodes: ["web", "input", "call", "a", "b"].map(node), edges: [
    edge(0, "web", "name", "input", "value.name"),
    edge(1, "input", "value", "call", "settings", {kind: "module-input"}),
    edge(2, "call", "settings.name", "a", "value"),
    edge(3, "call", "settings.other", "b", "value"),
  ]});
  assert.deepEqual(ids(graph.trace("web", "name", "up", Infinity)), new Set(["web", "input", "call", "a"]));
});

test("cycles terminate and retain connection evidence", () => {
  const graph = createGraph({nodes: ["a", "b"].map(node), edges: [
    edge(0, "a", "value", "b", "value"), edge(1, "b", "value", "a", "value"),
  ]});
  const result = graph.trace("a", "value", "both", Infinity);
  assert.equal(result.endpoints.length, 2);
  assert.equal(new Set(result.connections.map(connection => connection.edge.id)).size, 2);
});

test("large fan-out is bounded and disclosed; disconnected projects stay isolated", () => {
  const nodes = [node("root"), node("other-project")];
  const edges = [];
  for (let i = 0; i < 2000; i++) { nodes.push(node("peer" + i)); edges.push(edge(i, "root", "value", "peer" + i, "id")); }
  const graph = createGraph({nodes, edges});
  const result = graph.trace("root", "", "up", Infinity);
  assert.equal(result.endpoints.length, 80);
  assert.equal(result.truncated, true);
  assert.equal(ids(result).has("other-project"), false);
  assert.equal(graph.trace("root", "", "up", Infinity, 160).endpoints.length, 160);
});

test("individual branches expand and collapse without opening sibling branches", () => {
  const graph = createGraph({nodes: ["root", "left", "right", "left-leaf", "right-leaf"].map(node), edges: [
    edge(0, "root", "value", "left", "id"),
    edge(1, "root", "value", "right", "id"),
    edge(2, "left", "id", "left-leaf", "id"),
    edge(3, "right", "id", "right-leaf", "id"),
  ]});
  const branches = new Map();
  const initial = graph.trace("root", "", "up", 1, 80, branches);
  assert.deepEqual(ids(initial), new Set(["root", "left", "right"]));
  const left = initial.branches.find(branch => branch.endpoint.includes('"left"') && branch.expandable);
  branches.set(left.key, true);
  assert.deepEqual(ids(graph.trace("root", "", "up", 1, 80, branches)), new Set(["root", "left", "right", "left-leaf"]));
  branches.set(left.key, false);
  assert.deepEqual(ids(graph.trace("root", "", "up", 2, 80, branches)), new Set(["root", "left", "right", "right-leaf"]));
});

test("trace endpoints explain expression, unresolved, and source-defined stops", () => {
  const graph = createGraph({nodes: ["root", "expression", "leaf"].map(node), edges: [
    edge(0, "root", "first", "expression", "id", {certainty: "expression"}),
    edge(1, "root", "second", "missing", "id", {resolution: "unresolved"}),
    edge(2, "root", "third", "leaf", "id"),
  ]});
  const reasons = new Set(graph.trace("root", "", "up", 1, 80, new Map()).branches.map(branch => branch.reason));
  assert.equal(reasons.has("Expression boundary"), true);
  assert.equal(reasons.has("Unresolved source"), true);
  assert.equal(reasons.has("No source-defined continuation"), true);
});

test("relationship wording separates values, dependencies, mappings, and bindings", () => {
  const nodes = new Map([["move", {kind: "moved"}], ["resource", {kind: "resource"}]]);
  assert.equal(relationshipMeta(edge(0, "resource", "name", "value", "id"), nodes).label, "Value reference");
  assert.equal(relationshipMeta(edge(1, "resource", "depends_on", "value", "id"), nodes).label, "Explicit dependency");
  for (const field of ["depends_on[0]", "depends_on[12]"]) {
    assert.equal(relationshipMeta(edge(1, "resource", field, "value", "id"), nodes).label, "Explicit dependency");
  }
  for (const field of ["depends_on_extra[0]", "settings.depends_on[0]"]) {
    assert.equal(relationshipMeta(edge(1, "resource", field, "value", "id"), nodes).label, "Value reference");
  }
  assert.equal(relationshipMeta(edge(2, "move", "from", "value", "id"), nodes).label, "Address mapping");
  assert.equal(relationshipMeta(edge(3, "resource", "name", "value", "id", {kind: "module-input"}), nodes).label, "Local module input binding");
});


test("cache scope filters nodes, both edge directions, and diagnostics without mutating evidence", () => {
  const data = {nodes: [
    {id: "local", path: "main.tf"},
    {id: "cached", path: "project/.terraform/modules/a/main.tf"},
    {id: "windows", path: String.raw`project\.terraform\modules\b\main.tf`},
    {id: "similar", path: ".terraform-backup/main.tf"},
  ], edges: [edge(0, "local", "a", "cached", "b"), edge(1, "cached", "a", "local", "b"),
    edge(2, "local", "a", "unknown", "b"), edge(3, "local", "a", "similar", "b")],
    diagnostics: [{path: "main.tf"}, {path: ".terraform/modules/a/main.tf"}]};
  const filtered = filterTerraformCache(data, true);
  assert.deepEqual(filtered.nodes.map(n => n.id), ["local", "similar"]);
  assert.deepEqual(filtered.edges.map(e => e.id), [2, 3]);
  assert.equal(filtered.diagnostics.length, 1);
  assert.equal(data.nodes.length, 4);
  assert.equal(filterTerraformCache(data, false), data);
  assert.ok(!ids(createGraph(filtered).trace("local")).has("cached"));
});


test("field filtering keeps nested ancestors, matches paths case-insensitively, and preserves source trees", () => {
  const tree = [{path: "settings", children: [
    {path: "settings.region", children: []},
    {path: "settings.tags", children: [{path: "settings.tags.owner", children: []}]},
  ]}, {path: "name", children: []}];
  const original = JSON.stringify(tree);
  const result = filterFieldTree(tree, " OWNER ");
  assert.equal(result.length, 1);
  assert.equal(result[0].path, "settings");
  assert.equal(result[0].children.length, 1);
  assert.equal(result[0].children[0].path, "settings.tags");
  assert.equal(result[0].children[0].children[0].path, "settings.tags.owner");
  assert.deepEqual(filterFieldTree(tree, "settings"), [tree[0]]);
  assert.deepEqual(filterFieldTree(tree, "missing"), []);
  assert.equal(filterFieldTree(tree, "  "), tree);
  assert.equal(JSON.stringify(tree), original);
});
