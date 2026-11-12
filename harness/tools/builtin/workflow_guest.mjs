import { tr } from "./workflow_resources.mjs";
import * as vm from "node:vm";
//#region packages/workflow/workflow-ptc/src/realm.ts
/**
* Materializes script VM values as plain JSON and renders thrown values.
* Getters and proxy traps may execute inside the confined Node process;
* process isolation and cancellation belong to PTC, not the VM.
 * @module harness/workflow/realm
*/
/** Thrown by {@link materializeFromRealm}; the caller wraps it into the right `WorkflowError` code. */
var MaterializeError = class extends Error {
	path;
	reason;
	constructor(path, reason) {
		super(`${path}: ${reason}`);
		this.path = path;
		this.reason = reason;
		this.name = "MaterializeError";
	}
};
/**
* Render a thrown value to failure text without ever throwing: prefer the
* `stack` (host or realm — a realm error's `stack` is a plain string read),
* fall back to `message`, then `String()`. Reading those properties MAY run
* script code; if that code itself throws, a fixed label is returned instead.
* @param error - any value thrown in the host or guest realm.
* @returns human-readable text for the failure report; prefers the stack.
*/
function renderThrown(error) {
	try {
		const stack = error?.stack;
		if (typeof stack === "string" && stack.length > 0) return stack;
		const message = error?.message;
		if (typeof message === "string" && message.length > 0) return message;
		return String(error);
	} catch {
		return tr("workflow.guest.be1e972da2b1");
	}
}
/**
* Whether an object's prototype chain represents a plain data object: `null`, or a prototype
* whose own prototype is `null` (the realm's `Object.prototype` — which we
* cannot compare by identity across realms). A `Date`/`Map`/class instance
* has a longer chain and is rejected.
*/
function hasPlainPrototype(value) {
	const proto = Object.getPrototypeOf(value);
	if (proto === null) return true;
	return Object.getPrototypeOf(proto) === null;
}
/**
* Copy `value` (typically from the vm realm) into plain host JSON data. Root `undefined` is
* returned unchanged; nested `undefined` and values JSON cannot represent losslessly fail
* with the offending path. Property accessors run normally, and a throwing read is wrapped
* with its rendered failure.
*
* @param value - the realm value to materialize.
* @param root - the path label for the root value (error messages).
* @returns the host-realm copy (plain objects/arrays/scalars only).
* @throws {@link MaterializeError} for unsupported values, cycles, sparse arrays, exotic
*   prototypes, or property reads that throw.
*/
function materializeFromRealm(value, root = "value") {
	if (value === void 0) return void 0;
	try {
		return materialize(value, root, /* @__PURE__ */ new Set());
	} catch (error) {
		if (error instanceof MaterializeError) throw error;
		throw new MaterializeError(root, tr("workflow.guest.0e0fce781870", {p0: (renderThrown(error))}));
	}
}
function materialize(value, path, seen) {
	switch (typeof value) {
		case "boolean":
		case "string": return value;
		case "number":
			if (!Number.isFinite(value)) throw new MaterializeError(path, tr("workflow.guest.34e45502def4"));
			return value;
		case "bigint": throw new MaterializeError(path, tr("workflow.guest.7db56953de31"));
		case "function": throw new MaterializeError(path, tr("workflow.guest.7a9aad77b885"));
		case "symbol": throw new MaterializeError(path, tr("workflow.guest.38ce4675d1d8"));
		case "undefined": throw new MaterializeError(path, tr("workflow.guest.95906211d113"));
		case "object": break;
	}
	if (value === null) return null;
	const objectValue = value;
	if (seen.has(objectValue)) throw new MaterializeError(path, tr("workflow.guest.17af60c14c77"));
	seen.add(objectValue);
	try {
		if (Array.isArray(objectValue)) return materializeArray(objectValue, path, seen);
		return materializeObject(objectValue, path, seen);
	} finally {
		seen.delete(objectValue);
	}
}
function materializeArray(value, path, seen) {
	const out = [];
	for (let index = 0; index < value.length; index++) {
		if (!Object.hasOwn(value, index)) throw new MaterializeError(`${path}[${index}]`, tr("workflow.guest.217a8267a7f3"));
		out.push(materialize(value[index], `${path}[${index}]`, seen));
	}
	for (const key of Object.getOwnPropertyNames(value)) {
		if (key === "length") continue;
		const index = Number(key);
		if (!Number.isInteger(index) || String(index) !== key || index < 0 || index >= value.length) throw new MaterializeError(`${path}.${key}`, tr("workflow.guest.0967e40233ef"));
	}
	if (Object.getOwnPropertySymbols(value).length > 0) throw new MaterializeError(path, tr("workflow.guest.d8c974c1c8d6"));
	return out;
}
function materializeObject(value, path, seen) {
	if (!hasPlainPrototype(value)) throw new MaterializeError(path, tr("workflow.guest.1e37fe00f902"));
	if (Object.getOwnPropertySymbols(value).length > 0) throw new MaterializeError(path, tr("workflow.guest.d8c974c1c8d6"));
	const out = {};
	for (const key of Object.keys(value)) Object.defineProperty(out, key, {
		value: materialize(value[key], `${path}.${key}`, seen),
		enumerable: true,
		writable: true,
		configurable: true
	});
	return out;
}
//#endregion
//#region packages/llm/llm/src/error.ts
/**
* Harness error base with a stable machine-routable code and chained cause.
* Package errors extend it so tool results and replay can retain failure class.
 * @module harness/llm/error
*/
/**
* Base class for all harness errors. Carries a `code` (stable, programmatic —
* e.g. `NO_ADAPTER`, `INVALID_ARGS`, `INVARIANT`) distinct from the
* human-readable `message`, and supports `cause` chaining via the standard
* `ErrorOptions`. `name` defaults to the subclass constructor name.
*/
var HarnessError = class extends Error {
	/** Stable machine-routable failure class (e.g. `RATE_LIMIT`); route on this, never by parsing `message`. */
	code;
	constructor(message, code, options) {
		super(message, options);
		this.code = code;
		this.name = new.target.name;
	}
};
//#endregion
//#region packages/util/values/src/index.ts
/**
* Mark an unreachable closed-union branch.
* @param value - impossible value; an unhandled typed variant fails at the call site.
* @param context - optional switch-site label included in the failure message.
* @returns never; a runtime value that escaped its type always throws.
*/
function assertNever(value, context) {
	const rendered = JSON.stringify(value) ?? String(value);
	throw new Error(tr("workflow.guest.1425405a89a3", {p0: (context ? ` in ${context}` : ""), p1: (rendered)}));
}
/** Whether a realm-owned intrinsic prototype has a native constructor matching this engine's representation. */
function hasIntrinsicConstructor$1(prototype, name) {
	const constructor = Object.getOwnPropertyDescriptor(prototype, "constructor")?.value;
	if (typeof constructor !== "function") return false;
	try {
		return constructor.name === name && constructor.prototype === prototype && Function.prototype.toString.call(constructor) === Function.prototype.toString.call(name === "Array" ? Array : Object);
	} catch {
		return false;
	}
}
/** Whether a candidate is one realm's intrinsic `Object.prototype`. */
function isIntrinsicObjectPrototype$1(value) {
	return Object.getPrototypeOf(value) === null && hasIntrinsicConstructor$1(value, "Object");
}
/** Whether an array uses one realm's intrinsic `Array.prototype`, not a subclass or forged prototype. */
function hasPlainArrayPrototype$1(value) {
	const prototype = Object.getPrototypeOf(value);
	if (!Array.isArray(prototype) || !hasIntrinsicConstructor$1(prototype, "Array")) return false;
	const objectPrototype = Object.getPrototypeOf(prototype);
	return typeof objectPrototype === "object" && objectPrototype !== null && isIntrinsicObjectPrototype$1(objectPrototype);
}
/** Whether an object is a plain or null-prototype record from any JavaScript realm. */
function hasPlainObjectPrototype(value) {
	const prototype = Object.getPrototypeOf(value);
	return prototype === null || typeof prototype === "object" && isIntrinsicObjectPrototype$1(prototype);
}
/** Return every JSON-visible object key, or reject own data JSON would discard. */
function enumerableStringKeys(value) {
	const keys = Reflect.ownKeys(value);
	if (keys.some((key) => typeof key !== "string" || !Object.prototype.propertyIsEnumerable.call(value, key))) return void 0;
	return keys;
}
/** Validate lossless JSON iteratively, optionally materializing a detached snapshot. */
function walkJsonValue(value, detach) {
	const ancestors = /* @__PURE__ */ new Set();
	let root;
	const assign = (destination, item) => {
		if (destination === void 0) return;
		if (destination.kind === "root") root = item;
		else if (destination.kind === "array") destination.target[destination.index] = item;
		else Object.defineProperty(destination.target, destination.key, {
			value: item,
			enumerable: true,
			configurable: true,
			writable: true
		});
	};
	const tasks = [{
		kind: "visit",
		value,
		...detach ? { destination: { kind: "root" } } : {}
	}];
	for (let task = tasks.pop(); task !== void 0; task = tasks.pop()) {
		if (task.kind === "leave") {
			ancestors.delete(task.source);
			continue;
		}
		if (task.kind === "array-item") {
			if (!Object.prototype.hasOwnProperty.call(task.source, task.index)) return void 0;
			tasks.push({
				kind: "visit",
				value: task.source[task.index],
				...task.target === void 0 ? {} : { destination: {
					kind: "array",
					target: task.target,
					index: task.index
				} }
			});
			continue;
		}
		if (task.kind === "object-property") {
			tasks.push({
				kind: "visit",
				value: task.source[task.key],
				...task.target === void 0 ? {} : { destination: {
					kind: "object",
					target: task.target,
					key: task.key
				} }
			});
			continue;
		}
		const current = task.value;
		if (current === null) {
			assign(task.destination, null);
			continue;
		}
		if (typeof current === "boolean" || typeof current === "string") {
			assign(task.destination, current);
			continue;
		}
		if (typeof current === "number") {
			if (!Number.isFinite(current) || Object.is(current, -0)) return void 0;
			assign(task.destination, current);
			continue;
		}
		if (typeof current !== "object") return void 0;
		if (ancestors.has(current)) return void 0;
		if (Array.isArray(current)) {
			if (!hasPlainArrayPrototype$1(current)) return void 0;
			const length = current.length;
			if (Reflect.ownKeys(current).length !== length + 1) return void 0;
			const target = detach ? [] : void 0;
			if (target !== void 0) assign(task.destination, target);
			ancestors.add(current);
			tasks.push({
				kind: "leave",
				source: current
			});
			for (let index = length - 1; index >= 0; index--) tasks.push({
				kind: "array-item",
				source: current,
				index,
				...target === void 0 ? {} : { target }
			});
			continue;
		}
		if (!hasPlainObjectPrototype(current)) return void 0;
		const keys = enumerableStringKeys(current);
		if (keys === void 0) return void 0;
		const target = detach ? {} : void 0;
		if (target !== void 0) assign(task.destination, target);
		ancestors.add(current);
		tasks.push({
			kind: "leave",
			source: current
		});
		for (let index = keys.length - 1; index >= 0; index--) {
			const key = keys[index];
			/* v8 ignore next -- the loop is bounded by the captured key count. */
			if (key === void 0) return void 0;
			tasks.push({
				kind: "object-property",
				source: current,
				key,
				...target === void 0 ? {} : { target }
			});
		}
	}
	return detach ? root : true;
}
/**
* Test the same lossless JSON rules as {@link snapshotJsonValue} without detaching the value.
* @param value - candidate value to test.
* @returns whether the value survives a JSON round trip without loss.
*/
function isJsonValue(value) {
	return walkJsonValue(value, false) === true;
}
//#endregion
//#region packages/core/tools/src/json-schema.ts
/**
* Enforced JSON Schema subset shared by tool outputs, generated PTC mode
* types, subagents, and workflows. The subset accepts any JSON root, an
* annotation-only schema for unconstrained JSON, one scalar `type`, object
* `properties`/`required`/boolean `additionalProperties`, array `items`,
* type-correct scalar `enum`/`const`, and exact-one `oneOf`.
*
* Unsupported or misplaced keywords reject rather than being accepted without
* enforcement. Consumers that require an object root apply
* {@link assertObjectJsonSchema} before accepting input.
 * @module harness/tools/json-schema
*/
/**
* Thrown when a raw schema falls outside the enforced subset. `violations`
* lists every offending path instead of stopping at the first author error.
*/
var JsonSchemaError = class extends HarnessError {
	/** Individual schema violations in walk order. */
	violations;
	constructor(violations) {
		super(tr("workflow.guest.862c4625a002", {p0: (violations.join("; "))}), "UNSUPPORTED_SCHEMA");
		this.name = "JsonSchemaError";
		this.violations = violations;
	}
};
const CONSTRAINT_KEYWORDS = new Set([
	"type",
	"oneOf",
	"properties",
	"required",
	"additionalProperties",
	"items",
	"enum",
	"const"
]);
const ANNOTATION_KEYWORDS = new Set([
	"description",
	"title",
	"default",
	"examples"
]);
const SCHEMA_TYPES = [
	"object",
	"array",
	"string",
	"number",
	"integer",
	"boolean",
	"null"
];
/** Whether a realm-owned intrinsic prototype is backed by its native constructor. */
function hasIntrinsicConstructor(prototype, name) {
	const constructor = Object.getOwnPropertyDescriptor(prototype, "constructor")?.value;
	if (typeof constructor !== "function") return false;
	try {
		return constructor.name === name && constructor.prototype === prototype && Function.prototype.toString.call(constructor) === `function ${name}() { [native code] }`;
	} catch {
		return false;
	}
}
/** Whether a candidate is one realm's intrinsic `Object.prototype`. */
function isIntrinsicObjectPrototype(value) {
	return Object.getPrototypeOf(value) === null && hasIntrinsicConstructor(value, "Object");
}
/**
* Test for a realm-agnostic plain JSON record without accepting arrays or
* exotic objects.
* @param value - candidate record from any JavaScript realm.
* @returns Whether the value has a plain-object prototype chain.
*/
function isPlainJsonRecord(value) {
	if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
	try {
		const prototype = Object.getPrototypeOf(value);
		return prototype === null || typeof prototype === "object" && isIntrinsicObjectPrototype(prototype);
	} catch {
		return false;
	}
}
/** Whether an array uses one realm's intrinsic `Array.prototype`. */
function hasPlainArrayPrototype(value) {
	const prototype = Object.getPrototypeOf(value);
	if (!Array.isArray(prototype) || !hasIntrinsicConstructor(prototype, "Array")) return false;
	const objectPrototype = Object.getPrototypeOf(prototype);
	return typeof objectPrototype === "object" && objectPrototype !== null && isIntrinsicObjectPrototype(objectPrototype);
}
/** Return whether a record contains only own enumerable string keys. */
function hasOnlyEnumerableStringKeys(value) {
	try {
		return Reflect.ownKeys(value).every((key) => typeof key === "string" && Object.prototype.propertyIsEnumerable.call(value, key));
	} catch {
		return false;
	}
}
/**
* Test for an ordinary schema record whose keys survive JSON projection.
* @param value - candidate record from any JavaScript realm.
* @returns Whether the record has an intrinsic prototype and only own enumerable string keys.
*/
function isJsonSchemaRecord(value) {
	return isPlainJsonRecord(value) && hasOnlyEnumerableStringKeys(value);
}
/**
* Test for a dense ordinary array with no JSON-invisible decorations.
* @param value - candidate array from any JavaScript realm.
* @returns Whether the array is intrinsic, dense, and undecorated.
*/
function isPlainJsonArray(value) {
	if (!Array.isArray(value)) return false;
	try {
		if (!hasPlainArrayPrototype(value) || Reflect.ownKeys(value).length !== value.length + 1) return false;
		for (let index = 0; index < value.length; index++) if (!Object.hasOwn(value, index)) return false;
		return true;
	} catch {
		return false;
	}
}
/** Lossless finite JSON number, excluding negative zero. */
function isJsonNumber(value) {
	return typeof value === "number" && Number.isFinite(value) && !Object.is(value, -0);
}
/** Whether a scalar is valid for one declared schema type. */
function scalarMatches(type, value) {
	switch (type) {
		case "string": return typeof value === "string";
		case "number": return isJsonNumber(value);
		case "integer": return isJsonNumber(value) && Number.isInteger(value);
		case "boolean": return typeof value === "boolean";
		case "null": return value === null;
		/* v8 ignore next -- JsonSchemaScalarType is closed; this retains compile-time exhaustiveness. */
		default: return assertNever(type, "JsonSchemaType");
	}
}
/** Keywords that are invalid beside `oneOf`. */
const ONE_OF_SIBLING_KEYWORDS = [
	"properties",
	"required",
	"additionalProperties",
	"items",
	"enum",
	"const"
];
/** Validate object-only fields after its property schemas have been visited. */
function checkObjectSchemaTail(node, path, properties, violations) {
	const hasRequired = Object.hasOwn(node, "required");
	const required = hasRequired ? node.required : void 0;
	if (hasRequired) if (!isPlainJsonArray(required) || required.some((entry) => typeof entry !== "string")) violations.push(tr("workflow.validation.36c38830271d", {p0: (path)}));
	else {
		const declared = isJsonSchemaRecord(properties) ? properties : {};
		for (const key of required) if (!Object.hasOwn(declared, key)) violations.push(tr("workflow.validation.9a801bfb55fb", {p0: (path), p1: (key)}));
	}
	if (Object.hasOwn(node, "additionalProperties") && typeof node.additionalProperties !== "boolean") violations.push(tr("workflow.validation.55625f80bb6d", {p0: (path)}));
}
/** Collect every violation for one raw schema tree without using the JavaScript call stack. */
function checkSchemaNode(root, rootPath, violations, seen) {
	const tasks = [{
		kind: "enter",
		node: root,
		path: rootPath
	}];
	for (let task = tasks.pop(); task !== void 0; task = tasks.pop()) {
		if (task.kind === "leave") {
			seen.delete(task.node);
			continue;
		}
		if (task.kind === "one-of-tail") {
			for (const key of ONE_OF_SIBLING_KEYWORDS) if (Object.hasOwn(task.node, key)) violations.push(tr("workflow.validation.319f59b8940e", {p0: (task.path), p1: (key)}));
			continue;
		}
		if (task.kind === "object-tail") {
			checkObjectSchemaTail(task.node, task.path, task.properties, violations);
			continue;
		}
		const { node, path } = task;
		if (!isJsonSchemaRecord(node)) {
			violations.push(tr("workflow.validation.9429aabfbde5", {p0: (path)}));
			continue;
		}
		if (seen.has(node)) {
			violations.push(tr("workflow.validation.4bf87611b089", {p0: (path)}));
			continue;
		}
		seen.add(node);
		tasks.push({
			kind: "leave",
			node
		});
		for (const key of Object.keys(node)) {
			if (CONSTRAINT_KEYWORDS.has(key)) continue;
			if (ANNOTATION_KEYWORDS.has(key)) {
				try {
					if (!isJsonValue(node[key])) violations.push(tr("workflow.validation.0bc909506d53", {p0: (path), p1: (key)}));
				} catch {
					violations.push(tr("workflow.validation.0bc909506d53", {p0: (path), p1: (key)}));
				}
				continue;
			}
			violations.push(tr("workflow.validation.c2a5ef0d2fc6", {p0: (path), p1: (key)}));
		}
		if (Object.hasOwn(node, "description") && typeof node.description !== "string") violations.push(tr("workflow.validation.ed0e15feb909", {p0: (path)}));
		if (Object.hasOwn(node, "title") && typeof node.title !== "string") violations.push(tr("workflow.validation.f9e881b36b06", {p0: (path)}));
		const hasType = Object.hasOwn(node, "type");
		const hasOneOf = Object.hasOwn(node, "oneOf");
		if (hasType && hasOneOf) {
			violations.push(tr("workflow.validation.045c0ac969f5", {p0: (path)}));
			continue;
		}
		if (!hasType && !hasOneOf) {
			for (const key of ONE_OF_SIBLING_KEYWORDS) if (Object.hasOwn(node, key)) violations.push(tr("workflow.validation.63be167d4da0", {p0: (path), p1: (key)}));
			continue;
		}
		if (hasOneOf) {
			const oneOf = node.oneOf;
			tasks.push({
				kind: "one-of-tail",
				node,
				path
			});
			if (!isPlainJsonArray(oneOf) || oneOf.length < 2) violations.push(tr("workflow.validation.cda9437bd35e", {p0: (path)}));
			else for (let index = oneOf.length - 1; index >= 0; index--) tasks.push({
				kind: "enter",
				node: oneOf[index],
				path: `${path}.oneOf[${index}]`
			});
			continue;
		}
		const type = node.type;
		if (typeof type !== "string" || !SCHEMA_TYPES.includes(type)) {
			violations.push(Array.isArray(type) ? tr("workflow.validation.dee44a47ae35", {p0: (path)}) : tr("workflow.validation.222e4d963188", {p0: (path), p1: (SCHEMA_TYPES.join("/"))}));
			continue;
		}
		const schemaType = type;
		for (const [key, types] of Object.entries({
			properties: ["object"],
			required: ["object"],
			additionalProperties: ["object"],
			items: ["array"],
			enum: [
				"string",
				"number",
				"integer",
				"boolean",
				"null"
			],
			const: [
				"string",
				"number",
				"integer",
				"boolean",
				"null"
			]
		})) if (Object.hasOwn(node, key) && !types.includes(schemaType)) violations.push(tr("workflow.validation.d8aaf80ab8f4", {p0: (path), p1: (key), p2: (schemaType)}));
		switch (schemaType) {
			case "object": {
				const properties = Object.hasOwn(node, "properties") ? node.properties : void 0;
				tasks.push({
					kind: "object-tail",
					node,
					path,
					properties
				});
				if (Object.hasOwn(node, "properties")) if (!isJsonSchemaRecord(properties)) violations.push(tr("workflow.validation.4fb6dcb76da6", {p0: (path)}));
				else {
					const entries = Object.entries(properties);
					for (let index = entries.length - 1; index >= 0; index--) {
						const entry = entries[index];
						/* v8 ignore next -- the loop is bounded by the captured entry count. */
						if (entry === void 0) continue;
						tasks.push({
							kind: "enter",
							node: entry[1],
							path: `${path}.properties.${entry[0]}`
						});
					}
				}
				break;
			}
			case "array":
				if (Object.hasOwn(node, "items")) tasks.push({
					kind: "enter",
					node: node.items,
					path: `${path}.items`
				});
				break;
			case "string":
			case "number":
			case "integer":
			case "boolean":
			case "null": {
				const hasEnum = Object.hasOwn(node, "enum");
				const allowed = hasEnum ? node.enum : void 0;
				const enumValid = isPlainJsonArray(allowed) && allowed.length > 0 && allowed.every((entry) => scalarMatches(schemaType, entry));
				if (hasEnum && !enumValid) violations.push(tr("workflow.validation.f53c183f7343", {p0: (path), p1: (schemaType)}));
				const hasConst = Object.hasOwn(node, "const");
				const declaredConst = hasConst ? node.const : void 0;
				const constValid = scalarMatches(schemaType, declaredConst);
				if (hasConst) {
					if (!constValid) violations.push(tr("workflow.validation.e0e4480725f3", {p0: (path), p1: (schemaType)}));
					else if (enumValid && !allowed.includes(declaredConst)) violations.push(tr("workflow.validation.d4b134d35cb2", {p0: (path), p1: (path)}));
				}
				break;
			}
			/* v8 ignore next -- schemaType was narrowed from the closed SCHEMA_TYPES table above. */
			default: assertNever(schemaType, "JsonSchemaType");
		}
	}
}
/**
* Assert the enforced subset plus the object-root constraint retained by
* subagent and workflow structured outputs.
* @param schema - untrusted caller-supplied schema.
* @returns Assertion that the schema belongs to the supported subset and has an object root.
*/
function assertObjectJsonSchema(schema) {
	const violations = [];
	checkSchemaNode(schema, "schema", violations, /* @__PURE__ */ new Set());
	if (violations.length === 0 && (!isJsonSchemaRecord(schema) || !Object.hasOwn(schema, "type") || schema.type !== "object")) violations.push(tr("workflow.validation.de3fe05bbcc5"));
	if (violations.length > 0) throw new JsonSchemaError(violations);
}
//#endregion
//#region packages/workflow/workflow/src/index.ts
/**
* Typed error for workflow-seam failures. Extends {@link HarnessError}, so the
* `code` is machine-routable taxonomy. `fatal` drives the combinator
* discipline: `parallel()`/`pipeline()` re-throw a fatal error (a typo'd
* option or a tripped cap must kill the script loudly), and reserve the
* per-item `null` for child-run failures and ordinary in-stage script errors.
* Every {@link WorkflowErrorCode} is fatal; the flag exists so the
* distinction is explicit at every catch site rather than implied.
*/
var WorkflowError = class extends HarnessError {
	/** Whether combinators must propagate this error instead of nulling the item. */
	fatal;
	constructor(message, code, options) {
		super(message, code, options);
		this.name = "WorkflowError";
		this.fatal = options?.fatal ?? true;
	}
};
/**
* Whether combinators must re-throw `error` instead of mapping the item to `null`.
* @param error - any thrown value; fatality is host `instanceof` (unforgeable from a script realm).
* @returns true iff `error` is a {@link WorkflowError} whose `fatal` flag is set.
*/
function isFatalWorkflowError(error) {
	return error instanceof WorkflowError && error.fatal;
}
//#endregion
//#region packages/workflow/workflow-ptc/src/runtime.ts
/**
* Workflow VM hooks, child callbacks, ordinary concurrency limits and result serialization.
* PTC owns process confinement and cancellation. Fatal hook and provider failures propagate
* through combinators; ordinary child failures and stage errors become per-item nulls.
 * @module harness/workflow/runtime
*/
/** The `agent()` options the script may pass; everything else rejects loud. */
const SUPPORTED_AGENT_OPTIONS = new Set([
	"label",
	"phase",
	"schema",
	"provider",
	"model"
]);
/** Deferred Claude Code options we name explicitly in the rejection message. */
const DEFERRED_AGENT_OPTIONS = new Set([
	"effort",
	"isolation",
	"agentType"
]);
/** Flatten a child's final output blocks to text (the non-schema `agent()` result). */
function outputText(blocks) {
	return blocks.filter((block) => block.type === "text").map((block) => block.text).join("");
}
/** A short display label derived from the prompt when the script passes none. */
function defaultLabel(prompt) {
	const newline = prompt.indexOf("\n");
	const line = newline === -1 ? prompt : prompt.slice(0, newline);
	return line.length <= 48 ? line : `${line.slice(0, 47)}…`;
}
/**
* One script execution inside the confined Node process. The host owns
* cancellation and cleanup of any dropped child work.
*/
var WorkflowExecution = class {
	limits;
	observer;
	children;
	/** 1-based count of `agent()` calls started (the `agentsStarted` result field). */
	started = 0;
	activeSlots = 0;
	slotWaiters = [];
	currentPhase;
	context;
	compiled;
	constructor(meta, body, args, limits, observer, children) {
		this.limits = limits;
		this.observer = observer;
		this.children = children;
		try {
			this.compiled = new vm.Script(`(async () => {\n${body}\n})()`, {
				filename: `workflow:${meta.name}`,
				lineOffset: -1
			});
		} catch (error) {
			throw new WorkflowError(tr("workflow.guest.4466be4dd07d", {p0: (String(error))}), "SCRIPT_PARSE", { cause: error });
		}
		this.context = vm.createContext({}, { name: `workflow:${meta.name}` });
		const globals = {
			agent: (prompt, opts) => this.contain(this.agent(prompt, opts)),
			parallel: (thunks) => this.contain(this.parallel(thunks)),
			pipeline: (items, ...stages) => this.contain(this.pipeline(items, stages)),
			phase: (title) => {
				this.phase(title);
			},
			log: (message) => {
				this.log(message);
			},
			args
		};
		for (const [key, value] of Object.entries(globals)) this.context[key] = typeof value === "function" ? Object.freeze(value) : value;
	}
	/**
	* Run the script and materialize its JSON return value.
	* @returns A completed or error result; script failures never reject.
	*/
	async drive() {
		try {
			const scriptPromise = this.compiled.runInContext(this.context, { timeout: this.limits.syncTimeoutMs });
			const raw = await this.contain(Promise.resolve(scriptPromise));
			return {
				value: raw === void 0 ? null : this.materializeResult(raw),
				stopReason: "completed",
				agentsStarted: this.started
			};
		} catch (error) {
			return {
				value: null,
				stopReason: "error",
				error: renderThrown(error),
				agentsStarted: this.started
			};
		}
	}
	/**
	* Attach a no-op rejection consumer WITHOUT changing what the caller
	* receives: if the script drops the promise, a host rejection cannot become
	* an unhandled rejection that kills the process; if
	* the script does await it, it still observes the rejection.
	*/
	contain(promise) {
		promise.catch(() => {});
		return promise;
	}
	/** Materialize the script's return value; violations become RESULT_UNSERIALIZABLE. */
	materializeResult(raw) {
		try {
			return materializeFromRealm(raw, "workflow result");
		} catch (error) {
			/* v8 ignore next -- defensive rethrow arm: materializeFromRealm only throws MaterializeError */
			if (!(error instanceof MaterializeError)) throw error;
			throw new WorkflowError(tr("workflow.guest.b66917fde0fe", {p0: (error.message)}), "RESULT_UNSERIALIZABLE", { cause: error });
		}
	}
	/** Acquire one concurrency slot in FIFO order. */
	acquireSlot() {
		if (this.activeSlots < this.limits.maxConcurrentAgents) {
			this.activeSlots += 1;
			return Promise.resolve();
		}
		return new Promise((resolve) => {
			this.slotWaiters.push(() => {
				this.activeSlots += 1;
				resolve();
			});
		});
	}
	releaseSlot() {
		this.activeSlots -= 1;
		const next = this.slotWaiters.shift();
		if (next) next();
	}
	/** The `agent(prompt, opts)` hook. */
	async agent(rawPrompt, rawOpts) {
		if (typeof rawPrompt !== "string" || rawPrompt.length === 0) throw new WorkflowError(tr("workflow.guest.87b7fb1f70dc"), "INVALID_ARGUMENT");
		const opts = this.readAgentOptions(rawOpts);
		if (this.started >= this.limits.maxTotalAgents) throw new WorkflowError(tr("workflow.guest.f76a9beff056", {p0: (this.limits.maxTotalAgents)}), "AGENT_CAP");
		this.started += 1;
		const seq = this.started;
		const label = opts.label ?? defaultLabel(rawPrompt);
		const phase = opts.phase ?? this.currentPhase;
		await this.acquireSlot();
		try {
			let run;
			try {
				run = await this.children.startAgent({
					prompt: rawPrompt,
					...opts.schema !== void 0 ? { schema: opts.schema } : {},
					...opts.provider !== void 0 ? { provider: opts.provider } : {},
					...opts.model !== void 0 ? { model: opts.model } : {}
				});
			} catch (error) {
				throw new WorkflowError(tr("workflow.guest.e52741d93d4a", {p0: (renderThrown(error))}), "AGENT_START", { cause: error });
			}
			const info = {
				seq,
				label,
				...phase !== void 0 ? { phase } : {},
				childId: run.id
			};
			this.observer.agentStart(info);
			try {
				let result;
				try {
					result = await run.result;
				} catch (error) {
					this.observer.agentEnd({
						...info,
						outcome: "failed"
					});
					throw new WorkflowError(tr("workflow.guest.1a2c83cc150a", {p0: (renderThrown(error))}), "AGENT_RESULT", { cause: error });
				}
				if (result.stopReason === "completed") {
					if (opts.schema !== void 0) {
						if (result.structured === void 0) {
							this.observer.agentEnd({
								...info,
								outcome: "failed"
							});
							return null;
						}
						this.observer.agentEnd({
							...info,
							outcome: "completed"
						});
						return result.structured;
					}
					this.observer.agentEnd({
						...info,
						outcome: "completed"
					});
					return outputText(result.output);
				}
				this.observer.agentEnd({
					...info,
					outcome: "failed"
				});
				return null;
			} finally {
				await run.dispose();
			}
		} finally {
			this.releaseSlot();
		}
	}
	/** Materialize + validate the `agent()` options bag from the realm. */
	readAgentOptions(rawOpts) {
		if (rawOpts === void 0) return {};
		let opts;
		try {
			opts = materializeFromRealm(rawOpts, "agent() options");
		} catch (error) {
			/* v8 ignore next -- defensive rethrow arm: materializeFromRealm only throws MaterializeError */
			if (!(error instanceof MaterializeError)) throw error;
			throw new WorkflowError(tr("workflow.guest.35ee1a55968f", {p0: (error.message)}), "INVALID_ARGUMENT", { cause: error });
		}
		if (typeof opts !== "object" || opts === null || Array.isArray(opts)) throw new WorkflowError(tr("workflow.guest.72e782813ac9"), "INVALID_ARGUMENT");
		const record = opts;
		for (const key of Object.keys(record)) {
			if (SUPPORTED_AGENT_OPTIONS.has(key)) continue;
			if (DEFERRED_AGENT_OPTIONS.has(key)) throw new WorkflowError(tr("workflow.guest.cd5f6a712a24", {p0: (key)}), "UNSUPPORTED_OPTION");
			throw new WorkflowError(tr("workflow.guest.649b0c4d6fe9", {p0: (key)}), "UNSUPPORTED_OPTION");
		}
		for (const key of [
			"label",
			"phase",
			"provider",
			"model"
		]) if (record[key] !== void 0 && typeof record[key] !== "string") throw new WorkflowError(tr("runtime.validation.a7031865e71f", {p0: (key)}), "INVALID_ARGUMENT");
		let schema;
		if (record.schema !== void 0) try {
			assertObjectJsonSchema(record.schema);
			schema = record.schema;
		} catch (error) {
			/* v8 ignore next -- defensive rethrow arm: assertObjectJsonSchema only throws JsonSchemaError */
			if (!(error instanceof JsonSchemaError)) throw error;
			throw new WorkflowError(tr("workflow.guest.d98fc793bd00", {p0: (error.message)}), "UNSUPPORTED_SCHEMA", { cause: error });
		}
		return {
			...record.label !== void 0 ? { label: record.label } : {},
			...record.phase !== void 0 ? { phase: record.phase } : {},
			...record.provider !== void 0 ? { provider: record.provider } : {},
			...record.model !== void 0 ? { model: record.model } : {},
			...schema !== void 0 ? { schema } : {}
		};
	}
	/** The `parallel(thunks)` hook: each thunk caught → `null`; fatal errors propagate. */
	async parallel(rawThunks) {
		if (!Array.isArray(rawThunks)) throw new WorkflowError(tr("workflow.guest.e844a70aa37a"), "INVALID_ARGUMENT");
		this.assertItemCap(rawThunks.length, "parallel()");
		const thunks = rawThunks.map((thunk, index) => {
			if (typeof thunk !== "function") throw new WorkflowError(tr("workflow.guest.749e654c9089", {p0: (index)}), "INVALID_ARGUMENT");
			return thunk;
		});
		return Promise.all(thunks.map(async (thunk) => {
			try {
				return await thunk();
			} catch (error) {
				if (isFatalWorkflowError(error)) throw error;
				return null;
			}
		}));
	}
	/** The `pipeline(items, ...stages)` hook: per-item stage chains, NO cross-stage barrier. */
	async pipeline(rawItems, rawStages) {
		if (!Array.isArray(rawItems)) throw new WorkflowError(tr("workflow.guest.8fb745db2a62"), "INVALID_ARGUMENT");
		this.assertItemCap(rawItems.length, "pipeline()");
		if (rawStages.length === 0) throw new WorkflowError(tr("workflow.guest.486a3e52c166"), "INVALID_ARGUMENT");
		const stages = rawStages.map((stage, index) => {
			if (typeof stage !== "function") throw new WorkflowError(tr("workflow.guest.8678dea1132f", {p0: (index)}), "INVALID_ARGUMENT");
			return stage;
		});
		return Promise.all(rawItems.map(async (item, index) => {
			let value = item;
			try {
				for (const stage of stages) value = await stage(value, item, index);
				return value;
			} catch (error) {
				if (isFatalWorkflowError(error)) throw error;
				return null;
			}
		}));
	}
	assertItemCap(length, hook) {
		if (length > this.limits.maxItemsPerCall) throw new WorkflowError(tr("workflow.guest.566b074527fb", {p0: (hook), p1: (length), p2: (this.limits.maxItemsPerCall)}), "ITEM_CAP");
	}
	/** The `phase(title)` hook: sets the current label for subsequent `agent()` calls and notifies observers. */
	phase(title) {
		if (typeof title !== "string" || title.length === 0) throw new WorkflowError(tr("workflow.guest.fb40d39a5ab8"), "INVALID_ARGUMENT");
		this.currentPhase = title;
		this.observer.phase(title);
	}
	/** The `log(message)` hook: narration to observers. */
	log(message) {
		if (typeof message !== "string") throw new WorkflowError(tr("workflow.guest.9ef9d951046e"), "INVALID_ARGUMENT");
		this.observer.log(message);
	}
};
//#endregion
//#region packages/workflow/workflow-ptc/src/guest.ts
/**
* Run a workflow with one progress batch in flight. Drain progress before child
* disposal and the terminal result; PTC and the host own cancellation and cleanup.
* @param host - JSON callbacks owned by this workflow run.
* @returns The script result after progress delivery; initialization failures reject.
*/
async function runWorkflowGuest(host) {
	const init = await host.begin({});
	let queued = [];
	let inFlight;
	let progressError;
	const flush = () => {
		if (inFlight !== void 0 || queued.length === 0) return;
		const batch = queued;
		queued = [];
		inFlight = host.progress(batch).then(() => {
			inFlight = void 0;
			flush();
		}, (error) => {
			progressError = renderThrown(error);
			queued = [];
			inFlight = void 0;
		});
	};
	const send = (event) => {
		if (progressError !== void 0) return;
		queued.push(event);
		flush();
	};
	const drain = async () => {
		while (inFlight !== void 0) await inFlight;
	};
	const result = await new WorkflowExecution(init.meta, init.body, init.args, init.limits, {
		phase: (title) => {
			send({
				type: "phase",
				title
			});
		},
		log: (message) => {
			send({
				type: "log",
				message
			});
		},
		agentStart: (info) => {
			send({
				type: "agent-start",
				info
			});
		},
		agentEnd: (info) => {
			send({
				type: "agent-end",
				info
			});
		}
	}, { async startAgent(request) {
		const { callId, childId } = await host.startChild(request);
		const result = host.childResult({ callId });
		result.catch(() => {});
		return {
			id: childId,
			result,
			async dispose() {
				await drain();
				await host.disposeChild({ callId });
			}
		};
	} }).drive();
	await drain();
	return progressError === void 0 ? result : {
		value: null,
		stopReason: "error",
		error: progressError,
		agentsStarted: result.agentsStarted
	};
}
//#endregion
export { runWorkflowGuest };
//# sourceURL=harness-workflow-guest.js
