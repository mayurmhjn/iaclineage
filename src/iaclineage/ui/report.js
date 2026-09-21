"use strict";

// Field paths are compared on segment boundaries, never by a loose prefix.
function fieldMatches(selected, candidate) {
  return !selected || selected === candidate ||
    candidate.startsWith(selected + ".") || candidate.startsWith(selected + "[") ||
    selected.startsWith(candidate + ".") || selected.startsWith(candidate + "[");
}

function flattenFields(fields) {
  return fields.flatMap(field => [field, ...flattenFields(field.children)]);
}

function filterFieldTree(fields, query) {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return fields;
  return fields.flatMap(field => {
    if ((field.path + " " + (field.provisionerType || "")).toLocaleLowerCase().includes(term)) return [field];
    const children = filterFieldTree(field.children, term);
    return children.length ? [{...field, children}] : [];
  });
}

const conditionalRoleLabel = role => ({condition: "Condition", true: "When true", false: "When false"})[role] || role;
const conditionalRoleText = edge => (edge.conditionalRoles || []).map(item => conditionalRoleLabel(item.role)).join(" / ");

function relationshipMeta(edge, nodes) {
  const roles = edge.conditionalRoles || [];
  if (roles.length) return roles.some(item => item.role === "condition") ? {
    label: "Condition reference", verb: " is tested within ",
    proof: "This declaration is referenced within a condition. It does not supply the resulting field value. The selected branch is unknown because expressions are not evaluated.",
  } : {
    label: "Possible value reference", verb: " is a possible input to ",
    proof: "This declaration is referenced within a possible result branch. The selected branch is unknown because expressions are not evaluated.",
  };
  const sourceKind = nodes.get(edge.source)?.kind;
  if (sourceKind === "moved") return {
    label: "Address mapping", verb: " is mapped by ",
    proof: "The moved block declares an address mapping. It does not describe runtime value flow.",
  };
  if (/^depends_on(?:\[\d+\])?$/.test(edge.sourceField)) return {
    label: "Explicit dependency", verb: " is required by ",
    proof: "The consumer explicitly declares depends_on. This establishes ordering only; no runtime value flow is claimed.",
  };
  if (edge.kind === "module-input") return {
    label: "Local module input binding", verb: " binds into ",
    proof: "The call argument binds to the child input variable. This is a source binding, not an evaluated value.",
  };
  return {
    label: "Value reference", verb: " supplies ",
    proof: "The source explicitly references this declaration or attribute. Resolution confirms the declaration, not provider-computed attributes or runtime values.",
  };
}

function createGraph(data) {
  const nodes = new Map(data.nodes.map(node => [node.id, node]));
  const outgoing = new Map(), incoming = new Map();
  for (const edge of data.edges) {
    for (const [map, key] of [[outgoing, edge.source], [incoming, edge.target]]) {
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(edge);
    }
  }
  const references = (id, direction) => (direction === "up" ? outgoing : incoming).get(id) || [];
  const endpointKey = (id, field) => JSON.stringify([id, field]);

  function trace(id, field = "", direction = "both", depth = 2, limit = 80, branchState = null) {
    const rootKey = endpointKey(id, field);
    const endpoints = new Map([[rootKey, {key: rootKey, id, field, layer: 0}]]);
    const connections = new Map();
    let truncated = false, frontierRemaining = false;
    for (const way of direction === "both" ? ["up", "down"] : [direction]) {
      const visited = new Set([rootKey]);
      let frontier = [endpoints.get(rootKey)];
      for (let hop = 1; frontier.length && hop <= nodes.size; hop++) {
        const next = [];
        for (const current of frontier) {
          const branchKey = JSON.stringify([current.key, way]);
          const defaultExpanded = depth === Infinity || hop <= depth;
          const expanded = current.layer === 0 || branchState === null ? defaultExpanded :
            branchState.has(branchKey) ? branchState.get(branchKey) : defaultExpanded;
          if (current.blockedReason) continue;
          if (!expanded) { frontierRemaining = true; continue; }
          const candidates = references(current.id, way).filter(edge =>
            fieldMatches(current.field, way === "up" ? edge.sourceField : edge.targetField));
          for (const edge of candidates) {
            const peerId = way === "up" ? edge.target : edge.source;
            const fromField = way === "up" ? edge.sourceField : edge.targetField;
            let peerField = way === "up" ? edge.targetField : edge.sourceField;
            // Carry a selected object member through a direct whole-object
            // assignment/binding, so "input.a" cannot fan out into "input.b".
            if (fromField && (current.field.startsWith(fromField + ".") || current.field.startsWith(fromField + "["))) {
              peerField += current.field.slice(fromField.length);
            }
            const key = peerId === id && fieldMatches(field, peerField) ? rootKey : endpointKey(peerId, peerField);
            if (!endpoints.has(key) && endpoints.size >= limit) { truncated = true; continue; }
            if (!endpoints.has(key)) endpoints.set(key, {
              key, id: peerId, field: peerField, layer: way === "up" ? -hop : hop,
              blockedReason: edge.resolution !== "resolved" ? "Unresolved source" :
                edge.certainty !== "direct" ? "Expression boundary" : "",
            });
            const consumer = way === "up" ? current.key : key;
            const supplier = way === "up" ? key : current.key;
            const connectionKey = JSON.stringify([edge.id, consumer, supplier]);
            connections.set(connectionKey, {edge, consumer, supplier});
            // Only direct arrivals mark an endpoint visited; an earlier
            // expression must not block a later proven path to the same field.
            if (!visited.has(key) && edge.certainty === "direct" && edge.resolution === "resolved" && nodes.has(peerId)) {
              const endpoint = endpoints.get(key);
              if (endpoint.blockedReason) {
                endpoint.blockedReason = "";
                endpoint.layer = way === "up" ? -hop : hop;
              }
              visited.add(key);
              next.push(endpoint);
            }
          }
        }
        frontier = next;
      }
    }
    const branches = [];
    for (const endpoint of endpoints.values()) {
      const ways = endpoint.layer < 0 ? ["up"] : endpoint.layer > 0 ? ["down"] :
        direction === "both" ? ["up", "down"] : [direction];
      for (const way of ways) {
        const branchKey = JSON.stringify([endpoint.key, way]);
        let reason = endpoint.blockedReason || "";
        const candidates = reason ? [] : references(endpoint.id, way).filter(edge =>
          fieldMatches(endpoint.field, way === "up" ? edge.sourceField : edge.targetField));
        if (!reason && !nodes.has(endpoint.id)) reason = "Unresolved source";
        if (!reason && !candidates.length) reason = "No source-defined continuation";
        const defaultExpanded = depth === Infinity || Math.abs(endpoint.layer) < depth;
        const expanded = endpoint.layer === 0 ? true : branchState === null ? defaultExpanded :
          branchState.has(branchKey) ? branchState.get(branchKey) : defaultExpanded;
        branches.push({endpoint: endpoint.key, key: branchKey, direction: way,
          expandable: candidates.length > 0, expanded, reason});
        if (candidates.length && !expanded) frontierRemaining = true;
      }
    }
    return {endpoints: [...endpoints.values()], connections: [...connections.values()], branches, truncated, frontierRemaining};
  }
  return {nodes, references, trace};
}

// Group presentation only; traversal and branch identity remain field-specific.
function groupLineage(result) {
  const groups = new Map(), endpoints = new Map(), ports = new Map();
  for (const endpoint of result.endpoints) {
    const key = JSON.stringify([endpoint.layer, endpoint.id]);
    if (!groups.has(key)) groups.set(key, {key, id: endpoint.id, layer: endpoint.layer, fields: new Map()});
    endpoints.set(endpoint.key, {endpoint, group: groups.get(key)});
  }
  function port(key, field, edgeId) {
    const {endpoint, group} = endpoints.get(key);
    field = endpoint.field || field;
    if (!group.fields.has(field)) group.fields.set(field, {field, endpoints: new Map(), edges: new Set()});
    const row = group.fields.get(field);
    row.endpoints.set(key, endpoint);
    if (edgeId !== undefined) row.edges.add(edgeId);
    return {group, row};
  }
  for (const connection of result.connections) ports.set(connection, {
    supplier: port(connection.supplier, connection.edge.targetField, connection.edge.id),
    consumer: port(connection.consumer, connection.edge.sourceField, connection.edge.id),
  });
  for (const [key, {endpoint, group}] of endpoints) {
    if (![...group.fields.values()].some(row => row.endpoints.has(key))) port(key, endpoint.field);
  }
  return {groups: [...groups.values()], ports};
}

// Native Node test runner exercises the exact traversal used in the browser.
if (typeof module !== "undefined" && module.exports) module.exports = {filterFieldTree, filterTerraformCache, createGraph, fieldMatches, flattenFields, relationshipMeta, groupLineage};
if (typeof document !== "undefined") startReport(JSON.parse(document.getElementById("lineage-data").textContent));

// Apply one source scope to every report surface without changing exported evidence.
function filterTerraformCache(data, hide) {
  if (!hide) return data;
  const cached = path => /(^|[\\/])\.terraform([\\/]|$)/.test(path || "");
  const hidden = new Set(data.nodes.filter(node => cached(node.path)).map(node => node.id));
  return {...data,
    nodes: data.nodes.filter(node => !hidden.has(node.id)),
    edges: data.edges.filter(edge => !hidden.has(edge.source) && !hidden.has(edge.target) && !cached(edge.location)),
    diagnostics: data.diagnostics.filter(item => !cached(item.path)),
  };
}

function startReport(data) {
  const originalData = data;
  let graph = createGraph(data);
  const originalGraph = graph;
  let byEdge = new Map(data.edges.map(edge => [edge.id, edge]));
  const diagnosticsByPath = new Map();
  for (const diagnostic of data.diagnostics) {
    if (!diagnosticsByPath.has(diagnostic.path)) diagnosticsByPath.set(diagnostic.path, []);
    diagnosticsByPath.get(diagnostic.path).push(diagnostic);
  }
  const state = {selected: null, field: "", edge: null, direction: "both", depth: 1,
    tab: "fields", expanded: new Set(), branches: new Map(), openCards: new Set(),
    page: 0, limit: 80, history: []};
  let lineageObserver;
  const pageSize = 60;
  const $ = id => document.getElementById(id);
  // Keep the investigation flow compact: context, trace, connection evidence,
  // then detail tabs. Panels remain controlled by the same tab logic.
  const objectView = $("object-view"), focusedLineage = $("focused-lineage"),
    detailTabs = objectView.querySelector(".tabs"), detailPanels = [...objectView.querySelectorAll(".detail-panel")],
    connectionTable = $("lineage-table");
  objectView.insertBefore(focusedLineage, detailPanels[0]);
  connectionTable.classList.add("connection-evidence-panel");
  objectView.append(connectionTable, detailTabs, ...detailPanels);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text, action, className) => {
    const node = element("button", text, className);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  };
  const badge = (text, type = "") => element("span", text, "badge " + type);
  const statusBadge = ([text, type]) => badge(text, type + " status-badge");
  const setEmptyState = (container, title, copy, action) => {
    container.replaceChildren(element("strong", title, "empty-title"), element("p", copy, "empty-copy"));
    if (action) container.append(element("p", action, "empty-action"));
  };
  const code = text => element("code", text);
  const announce = text => { $("announcement").textContent = text; };
  async function copyLocation(location, trigger) {
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(location);
        copied = true;
      }
    } catch (_) { /* Fall through to the file://-safe selection fallback. */ }
    if (!copied) {
      const input = element("textarea");
      input.value = location;
      input.setAttribute("readonly", "");
      input.setAttribute("aria-hidden", "true");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
      input.remove();
    }
    trigger.textContent = copied ? "Copied" : "Copy failed";
    trigger.classList.toggle("copy-failed", !copied);
    announce(copied ? "Copied source location " + location : "Could not copy source location.");
    window.setTimeout(() => {
      trigger.textContent = "Copy location";
      trigger.classList.remove("copy-failed");
    }, 1400);
  }
  const copyLocationButton = location => {
    const trigger = button("Copy location", () => { void copyLocation(location, trigger); }, "quiet copy-location");
    trigger.dataset.copyLocation = location;
    trigger.title = "Copy source location " + location;
    trigger.setAttribute("aria-label", "Copy source location " + location);
    return trigger;
  };
  const diagnostics = node => diagnosticsByPath.get(node.path) || [];
  const ownReferences = node => graph.references(node.id, "up");
  const shortAddress = address => {
    const local = address.split("::").pop();
    const match = local.match(/(?:^|\.)(resource|data|variable|output|local)\.(.*)$/);
    return match ? (match[1] === "resource" ? "" : match[1] + ".") + match[2] : local;
  };
  const addressOf = id => graph.nodes.get(id)?.address || id;
  const endpointName = (id, field) => shortAddress(addressOf(id)) + (field ? " · " + field : "");
  const issueStates = node => {
    const edges = ownReferences(node);
    const result = [];
    if (edges.some(edge => edge.resolution !== "resolved")) result.push(["Unresolved / ambiguous", "issue"]);
    if (diagnostics(node).length) result.push(["File diagnostic", "issue"]);
    if (edges.some(edge => edge.certainty !== "direct")) result.push(["Expression limits", "limit"]);
    if (!result.length) result.push([edges.length ? "References resolved" : "No upstream references", "neutral"]);
    return result;
  };
  function definitionList(items) {
    const list = element("dl");
    for (const [label, value] of items) {
      list.append(element("dt", label), element("dd", value));
    }
    return list;
  }
  function addOptions(id, values) {
    for (const value of [...new Set(values)].sort()) {
      const option = element("option", value);
      option.value = value;
      $(id).append(option);
    }
  }
  addOptions("project", data.nodes.map(node => node.project));
  addOptions("kind", data.nodes.map(node => node.kind));
  $("sensitive-warning").hidden = !data.includeSourceValues;
  $("privacy-note").textContent = data.includeSourceValues ?
    "Source values included by explicit opt-in. This file may contain secrets." :
    "Literal values omitted. Quoted map keys use positional labels. Include source values only when generating a trusted report.";

  function filteredNodes() {
    const query = $("search").value.trim().toLocaleLowerCase();
    const project = $("project").value, kind = $("kind").value, status = $("status").value;
    const result = data.nodes.filter(node => {
      if (project && node.project !== project || kind && node.kind !== kind) return false;
      if (query && !(node.address + " " + node.path).toLocaleLowerCase().includes(query)) return false;
      if (status === "diagnostic") return diagnostics(node).length > 0;
      if (status === "unresolved") return ownReferences(node).some(edge => edge.resolution !== "resolved");
      if (status === "expression") return ownReferences(node).some(edge => edge.certainty !== "direct");
      return true;
    });
    const sort = $("sort").value;
    return result.sort((a, b) => sort === "upstream" ? ownReferences(b).length - ownReferences(a).length :
      sort === "downstream" ? graph.references(b.id, "down").length - graph.references(a.id, "down").length :
      a[sort].localeCompare(b[sort]));
  }

  function renderExplorer() {
    const nodes = filteredNodes();
    const pages = Math.max(1, Math.ceil(nodes.length / pageSize));
    state.page = Math.min(state.page, pages - 1);
    const visible = nodes.slice(state.page * pageSize, (state.page + 1) * pageSize);
    $("result-count").textContent = nodes.length + " of " + data.nodes.length + " declarations";
    $("page-label").textContent = (state.page + 1) + " / " + pages;
    $("previous-page").disabled = state.page === 0;
    $("next-page").disabled = state.page + 1 >= pages;
    $("filter-count").textContent = ["project", "kind", "status"].some(id => $(id).value) ? "(active)" : "";
    const rows = document.createDocumentFragment(), navigation = document.createDocumentFragment(), groups = new Map();
    for (const node of visible) {
      const row = element("tr");
      const addressCell = element("td");
      const open = button("", () => selectNode(node.id), "row-link");
      open.append(code(node.address));
      addressCell.append(open);
      const sourceCell = element("td", undefined, "source-cell");
      sourceCell.append(code(node.location));
      const stateCell = element("td");
      issueStates(node).forEach(state => stateCell.append(statusBadge(state)));
      row.append(addressCell, element("td", node.kind), sourceCell,
        element("td", ownReferences(node).length + " up / " + graph.references(node.id, "down").length + " down"), stateCell);
      rows.append(row);
      const projectPath = node.project.replaceAll("/", " / ");
      const groupKey = node.project === "(root)" || projectPath === node.group ? node.group : node.project + " / " + node.group;
      if (!groups.has(groupKey)) groups.set(groupKey, []);
      groups.get(groupKey).push(node);
    }
    for (const [name, members] of groups) {
      const group = element("details", undefined, "nav-group");
      group.open = true;
      group.append(element("summary", name));
      for (const node of members) {
        const open = button("", () => selectNode(node.id), "nav-item");
        open.append(code(shortAddress(node.address)));
        open.title = node.address;
        open.setAttribute("aria-current", String(state.selected === node.id));
        group.append(open);
      }
      navigation.append(group);
    }
    $("declaration-rows").replaceChildren(rows);
    $("declaration-nav").replaceChildren(navigation);
    $("table-empty").hidden = nodes.length > 0;
    if (!nodes.length) {
      if (!data.nodes.length) setEmptyState($("table-empty"), "No declarations recovered", "No supported Terraform declarations were found in this report.", "Check the report diagnostics and verify the selected repository contains .tf source files.");
      else setEmptyState($("table-empty"), "No declarations match", "The current search or filters hide every declaration.", "Clear the filters or try a shorter name.");
    }
  }

  function setTab(name, focus = false) {
    state.tab = name;
    document.querySelectorAll("[data-tab]").forEach(tab => {
      const selected = tab.dataset.tab === name;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      $("panel-" + tab.dataset.tab).hidden = !selected;
      if (selected && focus) tab.focus();
    });
  }
  function browse(focus = true) {
    setMaximized(false);
    $("browse-view").hidden = false;
    $("object-view").hidden = true;
    $("evidence").hidden = true;
    $("app").classList.remove("has-selection");
    if (focus) $("workspace").focus();
  }
  function selectNode(id, field = "", remember = true) {
    const node = graph.nodes.get(id);
    if (!node) { announce("Unresolved declaration. No local source was indexed for this target."); return; }
    if (remember && state.selected) state.history.push({id: state.selected, field: state.field});
    if (state.selected !== id) $("field-search").value = "";
    state.selected = id;
    state.field = field;
    state.edge = null;
    state.expanded.clear();
    state.branches.clear();
    state.openCards.clear();
    state.limit = 80;
    const first = graph.references(id, "up").find(edge => fieldMatches(field, edge.sourceField)) ||
      graph.references(id, "down").find(edge => fieldMatches(field, edge.targetField));
    if (first) state.edge = first.id;
    $("app").classList.add("has-selection");
    $("browse-view").hidden = true;
    $("object-view").hidden = false;
    $("evidence").hidden = false;
    $("object-address").textContent = shortAddress(node.address);
    $("object-location").textContent = node.location;
    $("object-kind").textContent = node.kind;
    $("object-status").replaceChildren(
      badge(graph.references(id, "up").length + " upstream"),
      badge(graph.references(id, "down").length + " downstream"),
      ...issueStates(node).map(statusBadge));
    $("back").disabled = !state.history.length;
    $("panel-overview").replaceChildren(element("h2", "Declaration overview"),
      definitionList([["Canonical address", node.address], ["Project", node.project],
        ["Source group", node.group], ["Location", node.location], ["Kind", node.kind]]),
      element("p", "Resolved means the declaration was found in the scanned source. Provider-computed attributes and runtime values are not verified.", "muted"));
    const snippet = element("pre"); snippet.append(code(node.snippet)); $("panel-overview").append(snippet);
    $("panel-source").replaceChildren(element("h2", "Source"));
    if (data.includeSourceValues && node.sourceText) {
      const source = element("pre"); source.append(code(node.sourceText)); $("panel-source").append(source);
    } else $("panel-source").append(element("p", "Raw source was not included in this report. Generate a trusted interactive report with --include-source-values to include it. Values cannot be revealed from this structural-only file.", "muted"));
    renderDiagnostics($("panel-diagnostics"), diagnostics(node));
    renderReferences(node);
    renderFields();
    renderExplorer();
    renderLineage();
    renderEvidence();
    setTab("fields");
    $("workspace").focus({preventScroll: true});
    announce("Selected " + node.address);
  }

  function selectField(path) {
    state.field = state.field === path ? "" : path;
    state.limit = 80;
    const edge = graph.references(state.selected, "up").find(edge => fieldMatches(state.field, edge.sourceField)) ||
      graph.references(state.selected, "down").find(edge => fieldMatches(state.field, edge.targetField));
    state.edge = edge ? edge.id : null;
    renderFields();
    renderLineage();
    renderEvidence();
    announce("Tracing " + (state.field || "whole declaration"));
    [...$("field-rows").querySelectorAll("[data-field]")].find(item => item.dataset.field === path)?.focus({preventScroll: true});
  }

  function renderFields() {
    const node = graph.nodes.get(state.selected), rows = document.createDocumentFragment();
    const query = $("field-search").value.trim();
    const visible = filterFieldTree(node.fields, query);
    const originalFields = new Map(flattenFields(node.fields).map(field => [field.path, field]));
    const references = ownReferences(node);
    function appendFields(fields, level = 0, prefix = "") {
      for (const field of fields) {
        const row = element("tr", undefined, state.field === field.path ? "row-selected" : "");
        const isBlock = field.kind === "block";
        if (isBlock) row.classList.add("field-block");
        const cell = element("td"), content = element("div", undefined, "field-cell");
        content.style.setProperty("--level", level);
        if (field.children.length) {
          const toggle = button("", () => {
            state.expanded.has(field.path) ? state.expanded.delete(field.path) : state.expanded.add(field.path);
            renderFields();
            // Restore focus after the table rows are replaced.
            [...$("field-rows").querySelectorAll(".field-toggle")].find(item => item.dataset.path === field.path)?.focus();
          }, "field-toggle");
          toggle.dataset.path = field.path;
          toggle.setAttribute("aria-label", "Expand or collapse " + field.path);
          toggle.setAttribute("aria-expanded", String(Boolean(query) || state.expanded.has(field.path)));
          toggle.disabled = Boolean(query);
          const icon = svgElement("svg", {viewBox: "0 0 16 16", "aria-hidden": "true"});
          icon.append(svgElement("path", {d: "M6 3l5 5-5 5", fill: "none", stroke: "currentColor", "stroke-width": "1.5"}));
          toggle.append(icon); content.append(toggle);
        }
        const select = button("", () => selectField(field.path), "row-link");
        const name = field.path.slice(prefix.length).replace(/^\./, "");
        const provisioner = isBlock && !prefix && /^provisioner\[(\d+)\]$/.exec(name);
        select.append(code(provisioner ? `Provisioner ${Number(provisioner[1]) + 1} · ${field.provisionerType || "unknown type"}` : name));
        select.dataset.field = field.path;
        select.setAttribute("aria-pressed", String(state.field === field.path));
        select.title = "Trace " + field.path + " · " + field.location;
        content.append(select); cell.append(content);
        const evidence = element("td");
        const location = element("small", undefined, "field-location");
        location.append(code(field.location), document.createTextNode(" "), copyLocationButton(field.location));
        evidence.append(location);
        const refs = references.filter(edge => edge.sourceField === field.path);
        if (isBlock) {
          const original = originalFields.get(field.path);
          const summary = original.children.map(child => {
            const label = child.path.slice(field.path.length + 1);
            return label + (child.kind === "tuple" ? ` · ${child.children.length} entries` : "");
          }).join(", ");
          evidence.append(element("span", summary || "Empty block", "block-summary"));
          if (/^(provisioner\[\d+\]\.)?connection\[\d+\]$/.test(field.path) && node.kind === "resource") {
            evidence.append(element("small", prefix ? "Provisioner connection settings" : "Resource connection settings", "muted"));
          }
          const count = references.filter(edge => fieldMatches(field.path, edge.sourceField)).length;
          if (count) evidence.append(button(`Trace ${count} reference${count === 1 ? "" : "s"}`, () => selectField(field.path), "row-link"));
        }
        const matches = edge => isBlock ? fieldMatches(field.path, edge.sourceField) : edge.sourceField === field.path;
        const hiddenRefs = originalGraph.references(node.id, "up").filter(matches).length > references.filter(matches).length;
        if (hiddenRefs) evidence.append(element("span", "References hidden by .terraform filter", "muted small"));
        for (const edge of refs) {
          const ref = button("", () => selectEdge(edge.id), "row-link field-evidence");
          ref.append(code(edge.traversal || "Module input binding"));
          if (conditionalRoleText(edge)) ref.append(element("small", conditionalRoleText(edge), "muted"));
          evidence.append(ref);
        }
        appendContextEvidence(evidence, field, node);
        if (data.includeSourceValues && field.value !== null) {
          const value = element("pre", undefined, "field-value"); value.append(code(field.value)); evidence.append(value);
        } else if (!isBlock && !refs.length && !hiddenRefs && !field.contextReferences?.length) {
          evidence.append(element("span", field.children.length ? field.children.length + (field.children.length === 1 ? " field" : " fields") + (query ? " shown" : "") :
            field.kind === "expression" ? "Expression not evaluated" : "Value omitted", "muted"));
        }
        appendConditionals(evidence, field, refs);
        const type = element("td"); type.append(badge(field.kind, "neutral"));
        row.append(cell, evidence, type); rows.append(row);
        if (query || state.expanded.has(field.path)) appendFields(field.children, level + 1, field.path);
      }
    }
    appendFields(visible);
    $("field-rows").replaceChildren(rows);
    $("fields-empty").hidden = visible.length > 0;
    if (!visible.length) setEmptyState($("fields-empty"), query ? "No fields match" : "No fields recovered", query ? "The field filter does not match this declaration." : "This declaration has no source-defined fields to display.", query ? "Clear the field filter or try a shorter name." : "Inspect the References or Source tabs for additional evidence.");
    $("field-search").disabled = !node.fields.length;
    $("clear-field-search").disabled = !$("field-search").value;
    $("field-filter-status").hidden = !query;
    $("field-filter-status").textContent = query ? flattenFields(visible).length + " fields shown, including parent context. Selecting a field changes the trace." : "";
    const containers = flattenFields(node.fields).filter(field => field.children.length);
    $("all-fields").hidden = Boolean(query);
    $("all-fields").disabled = Boolean(query) || !containers.length;
    $("all-fields").textContent = containers.length && containers.every(field => state.expanded.has(field.path)) ? "Collapse all" : "Expand all";
  }

  function appendContextEvidence(container, field, node) {
    for (const ref of field.contextReferences || []) {
      const description = ref.traversal.startsWith("self.") ? `Attribute of the containing resource: ${node.address}.` : {
        "path.module": "Directory of the module containing this expression.",
        "path.root": "Root module directory.",
        "path.cwd": "Terraform working directory before any -chdir option.",
      }[ref.traversal];
      const item = element("details", undefined, "context-evidence");
      const summary = element("summary");
      summary.append(code(ref.traversal), document.createTextNode(" · Context"));
      const location = element("small", undefined, "conditional-location muted");
      location.append(code(ref.location), document.createTextNode(" "), copyLocationButton(ref.location));
      item.append(summary, element("p", description || "Contextual expression."),
        element("small", "Value not evaluated. No dependency edge is added.", "muted"), location);
      container.append(item);
    }
  }

  function appendConditionals(container, field, refs) {
    const conditionals = field.conditionals || [];
    const byId = new Map(conditionals.map(item => [item.id, item]));
    function append(parent, conditional) {
      const section = element("details", undefined, "conditional-details");
      section.append(element("summary", "Conditional expression"),
        element("p", "Selected branch: Unknown — expression not evaluated.", "muted"));
      for (const part of conditional.parts) {
        const row = element("div", undefined, "conditional-part");
        row.append(element("strong", conditionalRoleLabel(part.role)));
        const matches = refs.filter(edge => (edge.conditionalRoles || []).some(
          role => role.conditional === conditional.id && role.role === part.role));
        for (const edge of matches) {
          const ref = button("", () => selectEdge(edge.id), "row-link field-evidence");
          ref.append(code(edge.traversal)); row.append(ref);
        }
        if (!matches.length) row.append(element("span", $("hide-terraform").checked ? "No visible static references in this part." : "No static references in this part.", "muted"));
        if (data.includeSourceValues && part.value != null) {
          const value = element("pre", undefined, "field-value"); value.append(code(part.value)); row.append(value);
        }
        row.append(element("small", part.location, "conditional-location muted"));
        for (const child of conditionals) {
          const context = child.context.at(-1);
          if (context?.conditional === conditional.id && context.role === part.role) append(row, child);
        }
        section.append(row);
      }
      parent.append(section);
    }
    conditionals.filter(item => !byId.has(item.context.at(-1)?.conditional)).forEach(item => append(container, item));
  }

  function renderDiagnostics(container, items) {
    container.replaceChildren();
    if (!items.length) { container.append(element("p", "No parser diagnostics recorded.", "muted")); return; }
    container.append(element("p", "Diagnostics apply to the source file and may affect more than this declaration.", "muted"));
    for (const diagnostic of items) {
      const item = element("div", undefined, "diagnostic-item");
      item.append(badge(diagnostic.code, "issue"), element("p", diagnostic.message), code(diagnostic.location));
      container.append(item);
    }
  }
  function connectionButton(edge) {
    const open = button("", () => selectEdge(edge.id), "connection-button");
    open.dataset.edge = edge.id;
    open.setAttribute("aria-pressed", String(state.edge === edge.id));
    const relationship = relationshipMeta(edge, graph.nodes);
    open.append(code(endpointName(edge.target, edge.targetField)), document.createTextNode(relationship.verb),
      code(endpointName(edge.source, edge.sourceField)));
    open.append(element("small", relationship.label +
      (conditionalRoleText(edge) ? " · " + conditionalRoleText(edge) : "") +
      " · " + edge.resolution + (edge.certainty !== "direct" ? " · expression boundary" : "") + " · " + edge.location));
    return open;
  }
  function renderReferences(node) {
    const panel = $("panel-references");
    panel.replaceChildren();
    for (const [direction, heading] of [["up", "Uses upstream"], ["down", "Used by downstream"]]) {
      panel.append(element("h2", heading));
      const edges = graph.references(node.id, direction), list = element("div", undefined, "connection-list");
      edges.forEach(edge => list.append(connectionButton(edge)));
      if (!edges.length) list.append(element("p", "No direct static references recorded in this direction.", "muted"));
      panel.append(list);
    }
  }

  function selectEdge(id, focusEvidence = true) {
    state.edge = id;
    syncEdgeSelection();
    renderEvidence();
    if (!focusEvidence) { announce("Connection " + (id + 1) + " selected in graph and table."); return; }
    $("evidence-heading").focus({preventScroll: true});
    if (window.matchMedia("(max-width: 1400px)").matches) $("evidence").scrollIntoView({block: "start"});
  }
  function syncEdgeSelection() {
    document.querySelectorAll("[data-edges]").forEach(item => item.classList.toggle("selected", JSON.parse(item.dataset.edges).includes(state.edge)));
    document.querySelectorAll("[data-edge]").forEach(item => {
      const selected = Number(item.dataset.edge) === state.edge;
      item.classList.toggle("selected", selected);
      item.classList.toggle("dimmed", state.edge !== null && !selected && item.classList.contains("lineage-wire"));
      if (item.hasAttribute("aria-pressed")) item.setAttribute("aria-pressed", String(selected));
    });
  }
  function renderEvidence() {
    const container = $("evidence-content"), edge = byEdge.get(state.edge);
    container.replaceChildren();
    const contextNode = graph.nodes.get(edge ? edge.source : state.selected);
    const contextField = edge ? edge.sourceField : state.field;
    const contextFields = contextNode && contextField ? flattenFields(contextNode.fields).filter(field =>
      fieldMatches(contextField, field.path) && field.contextReferences?.length) : [];
    if (contextFields.length) {
      container.append(element("h3", "Field context"));
      for (const field of contextFields) appendContextEvidence(container, field, contextNode);
    }
    if (!edge) {
      if (contextFields.length) return;
      container.append(element("p", state.field ? "No reference evidence for this field." : "Select a field or connection to inspect its evidence.", "muted"));
      container.append(element("p", "Source-defined literals and provider-computed attributes may have no static upstream reference. This does not prove runtime independence.", "muted small"));
      return;
    }
    const relationship = relationshipMeta(edge, graph.nodes);
    const summary = element("div", undefined, "evidence-summary");
    summary.append(code(endpointName(edge.target, edge.targetField)), document.createTextNode(relationship.verb),
      code(endpointName(edge.source, edge.sourceField)));
    container.append(summary, definitionList([
      ["Source location", edge.location], ["Resolution", edge.resolution === "resolved" ? "Declaration resolved" : edge.resolution === "ambiguous" ? "Ambiguous: multiple declarations" : "Unresolved declaration"],
      ["Relationship", relationship.label],
      ...(conditionalRoleText(edge) ? [["Conditional role", conditionalRoleText(edge)],
        ["Selected branch", "Unknown — expression not evaluated"]] : []),
    ]), copyLocationButton(edge.location));
    if (edge.usage) {
      const source = element("pre"); source.append(code(edge.usage)); container.append(source);
    }
    for (const [label, id, field] of [["Open supplier", edge.target, edge.targetField], ["Open consumer", edge.source, edge.sourceField]]) {
      const open = button(label, () => selectNode(id, field));
      open.disabled = !graph.nodes.has(id);
      container.append(open, document.createTextNode(" "));
    }
    const explanation = element("details", undefined, "evidence-section");
    explanation.append(element("summary", "What this proves"), element("p", relationship.proof));
    explanation.open = edge.certainty !== "direct" || edge.resolution !== "resolved";
    if (edge.certainty !== "direct") explanation.append(badge("Expression boundary", "limit"),
      element("p", "This reference occurs inside an expression or instance/key selection. Exact value continuity is not proven; this branch stops here."));
    if (edge.resolution !== "resolved") explanation.append(element("p", edge.resolution === "ambiguous" ?
      "More than one declaration has this address. The report does not choose a target." :
      "The target was not found in local source. Remote modules are not fetched and missing declarations are never guessed."));
    container.append(explanation);
    const addresses = element("details", undefined, "evidence-section");
    addresses.append(element("summary", "Canonical endpoint addresses"),
      definitionList([["Supplier", addressOf(edge.target)], ["Consumer", addressOf(edge.source)],
        ["Consumer field", edge.sourceField || "Declaration-level reference"],
        ["Reference", edge.traversal || "Module input binding"], ["Referenced attribute", edge.targetField || "Whole declaration"]]));
    container.append(addresses);
  }

  function svgElement(tag, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    return node;
  }
  function updateLineageDetailsControl() {
    const details = [...$("lineage-canvas").querySelectorAll(".lineage-card > details")];
    const allOpen = details.length > 0 && details.every(item => item.open);
    $("all-lineage-details").textContent = allOpen ? "Collapse all details" : "Expand all details";
    $("all-lineage-details").disabled = !details.length || $("lineage-viewport").hidden;
  }
  function renderLineage() {
    const depth = state.depth === "all" ? Infinity : state.depth;
    const result = graph.trace(state.selected, state.field, state.direction, depth, state.limit, state.branches);
    $("lineage-heading").textContent = state.field ? "Lineage · " + state.field : "Focused lineage";
    $("whole-declaration").hidden = !state.field;
    $("depth").value = String(state.depth);
    document.querySelectorAll("[data-direction]").forEach(item => item.setAttribute("aria-pressed", String(item.dataset.direction === state.direction)));
    const canvas = $("lineage-canvas");
    lineageObserver?.disconnect();
    canvas.replaceChildren(); canvas.style.transform = "";
    const presentation = groupLineage(result);
    const layers = new Map(), positions = new Map(), rows = new Map();
    const uniqueEdges = [...new Map(result.connections.map(connection => [connection.edge.id, connection.edge])).values()];
    const numbers = new Map(uniqueEdges.map(edge => [edge.id, edge.id + 1]));
    if (state.edge !== null && !numbers.has(state.edge)) { state.edge = null; renderEvidence(); }
    for (const group of presentation.groups) {
      if (!layers.has(group.layer)) layers.set(group.layer, []);
      layers.get(group.layer).push(group);
    }
    const ordered = [...layers.keys()].sort((a, b) => a - b);
    const cardWidth = 220, columnWidth = 290;
    const width = (ordered.length - 1) * columnWidth + cardWidth + 64;
    canvas.style.width = width + "px";
    const svg = svgElement("svg", {width, "aria-hidden": "true", class: "lineage-wires"});
    const defs = svgElement("defs", {}), marker = svgElement("marker", {id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto"});
    marker.append(svgElement("path", {d: "M0 1L9 5L0 9z", fill: "#006b70"})); defs.append(marker); svg.append(defs);
    const wires = svgElement("g", {}); svg.append(wires); canvas.append(svg);
    ordered.forEach((layer, column) => layers.get(layer).forEach(group => {
      const node = graph.nodes.get(group.id);
      const card = element("div", undefined, "lineage-card" + (layer === 0 ? " selected" : "") + (!node ? " unresolved" : ""));
      card.style.left = (24 + column * columnWidth) + "px";
      positions.set(group, {x: 24 + column * columnWidth, column, card});
      const open = button("", () => selectNode(group.id), "row-link");
      open.append(code(shortAddress(addressOf(group.id)))); open.disabled = !node;
      card.append(open, element("small", layer === 0 ? "Selected declaration" : layer < 0 ? "Upstream · " + -layer + " hops" : "Downstream · " + layer + " hops"));
      if (node) card.append(element("small", node.project + " · " + node.group, "lineage-context"));
      for (const row of group.fields.values()) {
        const field = element("div", undefined, "lineage-field");
        field.dataset.edges = JSON.stringify([...row.edges]);
        field.append(element("code", row.field || "Whole declaration", "endpoint-field"));
        rows.set(row, field);
        const controls = element("div", undefined, "branch-controls"), shownReasons = new Set();
        for (const endpoint of row.endpoints.values()) {
          for (const branch of result.branches.filter(item => item.endpoint === endpoint.key)) {
            if (branch.expandable && layer !== 0) {
              const action = branch.expanded ? "Collapse" : "Expand";
              const toggle = button(action + " " + branch.direction, () => {
                state.branches.set(branch.key, !branch.expanded); renderLineage();
                [...canvas.querySelectorAll("[data-branch]")].find(item => item.dataset.branch === branch.key)?.focus({preventScroll: true});
              }, "quiet branch-toggle");
              toggle.dataset.branch = branch.key;
              toggle.setAttribute("aria-expanded", String(branch.expanded));
              toggle.setAttribute("aria-label", action + " " + branch.direction + " branch from " + endpointName(endpoint.id, endpoint.field));
              controls.append(toggle);
            } else if (branch.reason && (layer !== 0 || !row.edges.size) && !shownReasons.has(branch.reason)) {
              shownReasons.add(branch.reason);
              controls.append(element("span", branch.reason, "trace-stop " + (branch.reason === "Expression boundary" ? "limit" : branch.reason === "Unresolved source" ? "issue" : "")));
            }
          }
        }
        if (controls.childNodes.length) field.append(controls);
        card.append(field);
      }
      const details = element("details"); details.open = state.openCards.has(group.key);
      details.dataset.card = group.key;
      details.addEventListener("toggle", () => {
        if (details.isConnected) {
          details.open ? state.openCards.add(group.key) : state.openCards.delete(group.key);
          updateLineageDetailsControl();
        }
      });
      details.append(element("summary", "Details"), element("p", node ? node.location : "This target was not indexed."));
      card.append(details); canvas.append(card);
    }));
    updateLineageDetailsControl();
    const links = result.connections.map(connection => {
      const ports = presentation.ports.get(connection);
      const start = positions.get(ports.supplier.group), end = positions.get(ports.consumer.group);
      const label = button(String(numbers.get(connection.edge.id)), () => selectEdge(connection.edge.id, false), "lineage-connection");
      label.dataset.edge = connection.edge.id;
      label.setAttribute("aria-pressed", String(state.edge === connection.edge.id));
      label.setAttribute("aria-label", "Connection " + numbers.get(connection.edge.id) + ": " + relationshipMeta(connection.edge, graph.nodes).label + ", " + endpointName(connection.edge.target, ports.supplier.row.field) + " to " + endpointName(connection.edge.source, ports.consumer.row.field));
      canvas.append(label);
      return {connection, ports, start, end, label, rail: end.column !== start.column + 1};
    });
    function layoutCards() {
      // Non-adjacent and cyclic links use separate rails above the cards.
      const railHeight = links.filter(link => link.rail).length * 46;
      let height = railHeight;
      for (const layer of ordered) {
        let y = railHeight + 8;
        for (const group of layers.get(layer)) {
          const position = positions.get(group);
          position.y = y; position.card.style.top = y + "px";
          y += position.card.offsetHeight + 12;
        }
        height = Math.max(height, y);
      }
      wires.replaceChildren();
      let railIndex = 0;
      const computedLinks = links.map(({connection, ports, start, end, label, rail}) => {
        const fieldY = (position, row) => {
          const field = rows.get(row);
          return position.y + field.offsetTop + field.querySelector(".endpoint-field").offsetHeight / 2 + 8;
        };
        const x1 = start.x + start.card.offsetWidth, y1 = fieldY(start, ports.supplier.row);
        const x2 = end.x, y2 = fieldY(end, ports.consumer.row);
        let labelX, labelY, path;
        if (rail) {
          labelY = 23 + railIndex++ * 46;
          labelX = end.column > start.column ? (x1 + x2) / 2 : start.x + cardWidth / 2;
          path = `M${x1} ${y1}H${x1 + 14}V${labelY}H${x2 - 16}V${y2}H${x2}`;
        } else {
          const dx = Math.max(28, (x2 - x1) * 0.45);
          path = `M${x1} ${y1}C${x1 + dx} ${y1},${x2 - dx} ${y2},${x2} ${y2}`;
          labelX = (x1 + x2) / 2;
          labelY = (y1 + y2) / 2;
        }
        return {connection, label, x1, y1, x2, y2, labelX, labelY, path, rail, columnKey: `${start.column}->${end.column}`};
      });

      // Prevent label collisions between adjacent links in the same layer gap
      const columnGroups = new Map();
      for (const item of computedLinks) {
        if (!item.rail) {
          if (!columnGroups.has(item.columnKey)) columnGroups.set(item.columnKey, []);
          columnGroups.get(item.columnKey).push(item);
        }
      }
      for (const group of columnGroups.values()) {
        group.sort((a, b) => a.labelY - b.labelY);
        for (let i = 1; i < group.length; i++) {
          const prev = group[i - 1], curr = group[i];
          if (curr.labelY - prev.labelY < 28) {
            curr.labelY = prev.labelY + 28;
          }
        }
      }

      for (const item of computedLinks) {
        item.label.style.left = (item.labelX - item.label.offsetWidth / 2) + "px";
        item.label.style.top = (item.labelY - item.label.offsetHeight / 2) + "px";
        height = Math.max(height, item.labelY + 30, item.y1 + 20, item.y2 + 20);
        wires.append(svgElement("path", {
          d: item.path,
          class: "lineage-wire" + (item.connection.edge.certainty !== "direct" || item.connection.edge.resolution !== "resolved" ? " limit" : ""),
          "data-edge": item.connection.edge.id,
          "marker-end": "url(#arrow)"
        }));
        for (const [cx, cy] of [[item.x1 + 2, item.y1], [item.x2 - 2, item.y2]]) {
          wires.append(svgElement("circle", {cx, cy, r: 3, class: "lineage-port", "data-edge": item.connection.edge.id}));
        }
      }
      canvas.style.height = height + "px"; svg.setAttribute("height", height);
      syncEdgeSelection();
    }
    // Hidden graph measurements are deferred until the graph is shown again.
    if (!$("lineage-viewport").hidden) layoutCards();
    lineageObserver = new ResizeObserver(() => { if (!$("lineage-viewport").hidden) layoutCards(); });
    for (const {card} of positions.values()) lineageObserver.observe(card);
    const table = element("table"), caption = element("caption", "Visible connections. Numbers match the graph.", "sr-only");
    const head = element("thead"), headers = element("tr"), body = element("tbody");
    for (const title of ["Select", "Referenced field", "Relationship", "Consumer field", "Source / resolution"]) {
      const th = element("th", title); th.scope = "col"; headers.append(th);
    }
    head.append(headers);
    for (const edge of uniqueEdges) {
      const row = element("tr"); row.dataset.edge = edge.id;
      const select = button(String(numbers.get(edge.id)), () => selectEdge(edge.id, false));
      select.dataset.edge = edge.id; select.setAttribute("aria-pressed", String(state.edge === edge.id));
      select.setAttribute("aria-label", "Select connection " + numbers.get(edge.id));
      const cell = element("td"); cell.append(select); row.append(cell);
      const endpointCell = (id, field) => {
        const cell = element("td"); cell.append(code(addressOf(id)), element("strong", field || "Whole declaration")); return cell;
      };
      row.append(endpointCell(edge.target, edge.targetField), element("td", relationshipMeta(edge, graph.nodes).label), endpointCell(edge.source, edge.sourceField));
      const evidence = element("td"); evidence.append(code(edge.location), element("small", edge.resolution +
        (conditionalRoleText(edge) ? " · " + conditionalRoleText(edge) : "") +
        (edge.certainty !== "direct" ? " · expression boundary" : ""))); row.append(evidence); body.append(row);
    }
    table.append(caption, head, body);
    $("connection-list").replaceChildren(uniqueEdges.length ? table : element("p", "No static connections in this direction for the selected field.", "muted"));
    $("lineage-note").textContent = result.endpoints.length + " endpoints · " + uniqueEdges.length + " references. " +
      (result.truncated ? "Display limit reached; more connections are available. " : "") +
      (result.frontierRemaining ? "More hops are available. " : "") +
      "Only matching source fields are followed. Traces stop at expression limits and attributes with no source-defined continuation; provider behavior is not inferred.";
    $("more-lineage").hidden = !result.truncated;
    $("expand-lineage").disabled = !result.branches.some(branch => branch.expandable && !branch.expanded);
    syncEdgeSelection();
    centerSelected();
    return result;
  }

  function enablePanelResize(id) {
    const panel = $(id), handle = $("resize-" + id), app = $("app");
    let drag = null;
    const settings = () => {
      const vertical = id === "evidence" && window.matchMedia("(max-width: 1400px)").matches;
      return {axis: vertical ? "height" : "width", coordinate: vertical ? "clientY" : "clientX",
        direction: !vertical && id === "evidence" ? -1 : 1,
        min: vertical ? 160 : id === "evidence" ? 240 : 280,
        max: Math.floor(vertical ? innerHeight * .8 : Math.min(id === "evidence" ? 560 : 480, innerWidth * .35))};
    };
    const update = () => {
      if (!handle.getClientRects().length) return;
      const config = settings();
      handle.setAttribute("aria-orientation", config.axis === "height" ? "horizontal" : "vertical");
      handle.setAttribute("aria-valuemin", String(config.min));
      handle.setAttribute("aria-valuemax", String(config.max));
      const size = Math.round(panel.getBoundingClientRect()[config.axis]);
      handle.setAttribute("aria-valuenow", String(size));
      handle.setAttribute("aria-valuetext", size + " pixels " + (config.axis === "height" ? "high" : "wide"));
    };
    const resize = (value, config) => {
      app.style.setProperty("--" + id + "-" + config.axis, Math.max(config.min, Math.min(config.max, value)) + "px");
      update();
    };
    const finish = () => {
      const pointer = drag?.pointer;
      drag = null;
      document.body.classList.remove("resizing-panels");
      if (pointer !== undefined && handle.hasPointerCapture(pointer)) handle.releasePointerCapture(pointer);
    };
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || !event.isPrimary) return;
      const config = settings();
      drag = {...config, pointer: event.pointerId, start: event[config.coordinate],
        size: panel.getBoundingClientRect()[config.axis]};
      handle.setPointerCapture(event.pointerId);
      handle.focus({preventScroll: true});
      document.body.style.setProperty("--resize-cursor", config.axis === "height" ? "row-resize" : "col-resize");
      document.body.classList.add("resizing-panels");
      event.preventDefault();
    });
    handle.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.pointer) return;
      resize(drag.size + (event[drag.coordinate] - drag.start) * drag.direction, drag);
    });
    for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) handle.addEventListener(event, finish);
    handle.addEventListener("keydown", event => {
      const config = settings();
      const forward = config.axis === "height" ? "ArrowDown" : "ArrowRight";
      const backward = config.axis === "height" ? "ArrowUp" : "ArrowLeft";
      const delta = event.key === forward ? 16 : event.key === backward ? -16 : 0;
      if (!delta && event.key !== "Home" && event.key !== "End") return;
      event.preventDefault();
      resize(event.key === "Home" ? config.min : event.key === "End" ? config.max :
        panel.getBoundingClientRect()[config.axis] + delta * config.direction, config);
    });
    handle.addEventListener("dblclick", () => {
      app.style.removeProperty("--" + id + "-" + settings().axis);
      update();
    });
    window.addEventListener("resize", () => { finish(); update(); });
    new ResizeObserver(update).observe(panel);
  }
  enablePanelResize("declarations");
  enablePanelResize("evidence");

  function setMaximized(maximized) {
    $("app").classList.toggle("lineage-maximized", maximized);
    $("maximize-lineage").textContent = maximized ? "Restore layout" : "Maximize lineage";
    $("maximize-lineage").setAttribute("aria-pressed", String(maximized));
    centerSelected();
  }
  $("maximize-lineage").addEventListener("click", () => {
    setMaximized(!$("app").classList.contains("lineage-maximized"));
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && $("app").classList.contains("lineage-maximized")) {
      setMaximized(false);
      $("maximize-lineage").focus();
    }
  });
  $("toggle-declarations").addEventListener("click", () => {
    const hidden = $("app").classList.toggle("declarations-hidden");
    $("declarations").hidden = hidden;
    $("toggle-declarations").textContent = hidden ? "Show declarations" : "Hide declarations";
    $("toggle-declarations").setAttribute("aria-expanded", String(!hidden));
    centerSelected();
  });
  $("hide-terraform").addEventListener("change", () => {
    data = filterTerraformCache(originalData, $("hide-terraform").checked);
    $("source-scope-note").hidden = !$("hide-terraform").checked;
    graph = createGraph(data);
    byEdge = new Map(data.edges.map(edge => [edge.id, edge]));
    state.page = 0;
    state.history = state.history.filter(item => graph.nodes.has(item.id));
    const selected = state.selected, field = state.field, tab = state.tab;
    if (selected && graph.nodes.has(selected) && !$("object-view").hidden) {
      selectNode(selected, field, false);
      setTab(tab);
    } else {
      state.selected = null;
      state.edge = null;
      browse(false);

    }
    renderExplorer();
    renderReportDiagnostics();
    announce($("hide-terraform").checked ? ".terraform content hidden across this report. Reference counts and traces are filtered." : "All source content shown.");
  });
  ["search", "project", "kind", "status", "sort"].forEach(id => $(id).addEventListener(id === "search" ? "input" : "change", () => {
    state.page = 0; renderExplorer();
    if (window.matchMedia("(max-width: 700px)").matches && id === "search") browse(false);
  }));
  $("clear-filters").addEventListener("click", () => {
    ["search", "project", "kind", "status"].forEach(id => { $(id).value = ""; });
    state.page = 0; renderExplorer();
  });
  $("previous-page").addEventListener("click", () => { state.page--; renderExplorer(); });
  $("next-page").addEventListener("click", () => { state.page++; renderExplorer(); });
  $("browse").addEventListener("click", browse);
  $("return-to-trace").addEventListener("click", () => {
    const container = $("lineage-viewport").hidden ? $("connection-list") : $("lineage-canvas");
    const connection = [...container.querySelectorAll("button[data-edge]")].find(item => Number(item.dataset.edge) === state.edge);
    const target = connection || $("lineage-view");
    target.focus({preventScroll: true});
    target.scrollIntoView({block: "center", inline: "nearest"});
  });
  $("back").addEventListener("click", () => { const previous = state.history.pop(); if (previous) selectNode(previous.id, previous.field, false); });
  $("copy-address").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(addressOf(state.selected)); announce("Address copied."); }
    catch { announce("Clipboard unavailable. Copy the canonical address from Overview."); setTab("overview"); }
  });
  document.querySelectorAll("[data-tab]").forEach(tab => {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
    tab.addEventListener("keydown", event => {
      const tabs = [...document.querySelectorAll("[data-tab]")], index = tabs.indexOf(tab);
      const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length :
        event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
      if (next !== null) { event.preventDefault(); setTab(tabs[next].dataset.tab, true); }
    });
  });
  $("field-search").addEventListener("input", renderFields);
  $("clear-field-search").addEventListener("click", () => {
    $("field-search").value = "";
    renderFields();
    $("field-search").focus();
  });
  $("all-fields").addEventListener("click", () => {
    const fields = flattenFields(graph.nodes.get(state.selected).fields).filter(field => field.children.length);
    const collapse = fields.every(field => state.expanded.has(field.path));
    state.expanded = new Set(collapse ? [] : fields.map(field => field.path));
    renderFields();
  });
  $("whole-declaration").addEventListener("click", () => {
    selectField(state.field);
    $("lineage-view").focus({preventScroll: true});
  });
  $("all-lineage-details").addEventListener("click", () => {
    const details = [...$("lineage-canvas").querySelectorAll(".lineage-card > details")];
    const expand = details.some(item => !item.open);
    for (const item of details) {
      item.open = expand;
      expand ? state.openCards.add(item.dataset.card) : state.openCards.delete(item.dataset.card);
    }
    updateLineageDetailsControl();
    announce((expand ? "Expanded" : "Collapsed") + " details for all displayed lineage cards.");
  });
  document.querySelectorAll("[data-direction]").forEach(item => item.addEventListener("click", () => {
    state.direction = item.dataset.direction; renderLineage();
  }));
  $("depth").addEventListener("change", () => { state.depth = $("depth").value === "all" ? "all" : Number($("depth").value); renderLineage(); });
  $("expand-lineage").addEventListener("click", () => {
    const result = renderLineage();
    result.branches.filter(branch => branch.expandable && !branch.expanded).forEach(branch => state.branches.set(branch.key, true));
    renderLineage();
  });
  function centerSelected() {
    const viewport = $("lineage-viewport"), card = viewport.querySelector(".lineage-card.selected");
    if (!card || viewport.hidden) return;
    const bounds = card.getBoundingClientRect(), frame = viewport.getBoundingClientRect();
    viewport.scrollBy({left: bounds.left - frame.left - Math.max(0, (viewport.clientWidth - bounds.width) / 2), top: bounds.top - frame.top - 8});
  }
  $("center-lineage").addEventListener("click", centerSelected);
  $("lineage-view").addEventListener("change", () => {
    const view = $("lineage-view").value;
    $("lineage-viewport").hidden = view === "table";
    $("lineage-table").hidden = view === "graph";
    $("center-lineage").disabled = $("fit-lineage").disabled = view === "table";
    renderLineage();
  });
  $("fit-lineage").addEventListener("click", () => {
    const canvas = $("lineage-canvas"), viewport = $("lineage-viewport");
    const scale = Math.max(.75, Math.min(1, viewport.clientWidth / canvas.offsetWidth));
    canvas.style.transform = "scale(" + scale + ")";
    centerSelected();
    announce("Diagram at " + Math.round(scale * 100) + "% size. Scroll for remaining branches.");
  });
  $("reset-lineage").addEventListener("click", () => {
    state.depth = 1; state.direction = "both"; state.limit = 80; state.branches.clear(); state.openCards.clear(); renderLineage();
    centerSelected();
  });
  $("more-lineage").addEventListener("click", () => { state.limit += 80; renderLineage(); });
  renderExplorer();
  function renderReportDiagnostics() {
    $("report-diagnostics-title").textContent = "Report diagnostics (" + data.diagnostics.length + ")";
    renderDiagnostics($("report-diagnostics"), data.diagnostics);
  }
  renderReportDiagnostics();
}
