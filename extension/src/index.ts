/**
 * SwitchBoard — EasyEDA Pro Extension
 *
 * Architecture: Extension reads schematic via EDA API,
 * stores it on a global variable. The iframe reads it
 * from window.parent and handles all backend HTTP.
 */

// ── Menu handlers ──

export async function openChat(): Promise<void> {
	await storeSchematicGlobally();
	eda.sys_IFrame.openIFrame('./iframe/index.html', 420, 600);
}

export async function syncSchematic(): Promise<void> {
	await storeSchematicGlobally();
	eda.sys_IFrame.openIFrame('./iframe/index.html', 420, 600);
}

export async function designReview(): Promise<void> {
	await storeSchematicGlobally();
	eda.sys_IFrame.openIFrame('./iframe/index.html#review', 420, 600);
}

export async function suggestSubcircuit(): Promise<void> {
	await storeSchematicGlobally();
	eda.sys_IFrame.openIFrame('./iframe/index.html#subcircuits', 420, 600);
}

// eslint-disable-next-line unused-imports/no-unused-vars
export function activate(_status?: 'onStartupFinished', _arg?: string): void {
	(globalThis as any).__switchboard_applyEdits = applyEdits;
	(globalThis as any).__switchboard_refreshContext = refreshContext;
	(globalThis as any).__switchboard_runDrc = runDrcFromIframe;
	(globalThis as any).__switchboard_fixDrc = fixDrcAuto;
	startEditPoller();
}

// Also set it immediately at module load time (activate may not be called)
(globalThis as any).__switchboard_applyEdits = applyEdits;
(globalThis as any).__switchboard_refreshContext = refreshContext;
(globalThis as any).__switchboard_runDrc = runDrcFromIframe;
(globalThis as any).__switchboard_fixDrc = fixDrcAuto;
startEditPoller();

/**
 * Poll for edit requests from the iframe.
 * Functions can't cross iframe boundaries, but data objects can.
 * The iframe writes { actions, id } to __switchboard_edit_request,
 * and we poll for new requests every 200ms.
 */
let _lastProcessedEditId = 0;

function startEditPoller(): void {
	if ((globalThis as any).__switchboard_poller_active) return;
	(globalThis as any).__switchboard_poller_active = true;

	setInterval(async () => {
		try {
			const request = (globalThis as any).__switchboard_edit_request;
			if (!request || !request.id || request.id === _lastProcessedEditId) return;

			_lastProcessedEditId = request.id;
			const actions: EditAction[] = request.actions || [];
			if (actions.length === 0) return;

			try {
				const results = await applyEdits(actions);
				(globalThis as any).__switchboard_edit_results = results;
				(globalThis as any).__switchboard_edit_results_ts = Date.now();
			}
			catch (e: any) {
				(globalThis as any).__switchboard_edit_results = [{ ok: false, msg: '', error: e?.message || String(e) }];
				(globalThis as any).__switchboard_edit_results_ts = Date.now();
			}
		}
		catch { /* ignore polling errors */ }
	}, 200);
}

// ── Store schematic on a global for the iframe to read ──

async function storeSchematicGlobally(): Promise<void> {
	// Ensure the edit applier is always available
	(globalThis as any).__switchboard_applyEdits = applyEdits;

	try {
		const context = await readSchematicContextAsync();
		(globalThis as any).__switchboard_context = context;
		(globalThis as any).__switchboard_timestamp = Date.now();
	}
	catch (e: any) {
		(globalThis as any).__switchboard_context = null;
		(globalThis as any).__switchboard_error = e?.message || String(e);
		eda.sys_ToastMessage.showMessage(`EDA read error: ${e?.message || e}`, 'error' as any);
	}
}

/**
 * Re-read the live schematic from EasyEDA and update the global context.
 * Called by the iframe after edits or before chat messages to keep state in sync.
 * Returns the fresh context object.
 */
async function refreshContext(): Promise<SchematicContext | null> {
	try {
		const context = await readSchematicContextAsync();
		(globalThis as any).__switchboard_context = context;
		(globalThis as any).__switchboard_timestamp = Date.now();
		return context;
	}
	catch (e: any) {
		return (globalThis as any).__switchboard_context || null;
	}
}

/**
 * Run DRC check — callable from iframe via __switchboard_runDrc.
 * Returns a string summary of DRC issues for the AI to process.
 */
async function runDrcFromIframe(): Promise<string> {
	try {
		if (typeof eda?.sch_Drc?.check !== 'function') {
			return 'DRC API not available in this version of EasyEDA Pro';
		}

		const errors = await eda.sch_Drc.check(true, false, true);

		if (!errors || (Array.isArray(errors) && errors.length === 0)) {
			return 'DRC passed — no errors';
		}

		if (typeof errors === 'boolean') {
			return errors ? 'DRC passed' : 'DRC found issues';
		}

		// Also gather floating pin info by scanning all components
		const floatingPins: string[] = [];
		try {
			const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
			if (allComps) {
				for (const comp of allComps) {
					const ref = comp.getState_Designator?.() || '?';
					const pins = await comp.getAllPins?.();
					if (!pins) continue;
					for (const pin of pins) {
						// Check if pin has no connect flag and is not connected
						const isNC = pin.getState_NoConnected?.();
						if (!isNC) {
							const pinNum = pin.getState_PinNumber?.() || '?';
							const pinName = pin.getState_PinName?.() || '';
							// We can't easily check if a pin is truly floating without netlist cross-ref
							// but we can report pins that the EDA flagged
						}
					}
				}
			}
		}
		catch { /* pin scanning failed */ }

		const issues = (errors as any[]).map((err: any) =>
			err?.message || err?.msg || err?.description || JSON.stringify(err)
		);

		return `DRC found ${issues.length} issues:\n${issues.slice(0, 30).join('\n')}`;
	}
	catch (e: any) {
		return `DRC error: ${e?.message || e}`;
	}
}

/**
 * Auto-fix DRC issues — callable from iframe via __switchboard_fixDrc.
 * Directly fixes floating pins (marks NC) and reports unfixable issues.
 */
async function fixDrcAuto(): Promise<string> {
	const results: string[] = [];
	const unfixable: string[] = [];

	try {
		const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
		if (!allComps || !Array.isArray(allComps)) {
			return 'Cannot access components';
		}

		// Build set of connected pins from netlist
		const connectedPins = new Set<string>();
		const ctx = (globalThis as any).__switchboard_context;
		if (ctx?.rawNetlist) {
			try {
				const netlist = JSON.parse(ctx.rawNetlist);
				for (const [, compData] of Object.entries(netlist)) {
					const cd = compData as any;
					if (!cd?.props || !cd?.pins) continue;
					const ref = cd.props.Designator || '';
					for (const [pinNum, netName] of Object.entries(cd.pins)) {
						if (netName) connectedPins.add(`${ref}.${pinNum}`);
					}
				}
			}
			catch { /* netlist parse failed */ }
		}

		// Scan all components for floating pins and missing properties
		let ncCount = 0;
		for (const comp of allComps) {
			const ref = comp.getState_Designator?.() || '?';
			const value = comp.getState_Name?.() || '';
			const footprint = comp.getState_Footprint?.();

			// Check missing footprint
			if (!footprint || (!footprint.uuid && !footprint.libraryUuid)) {
				unfixable.push(`${ref}: missing footprint (assign manually via library)`);
			}

			// Check empty value
			if (!value || value === '{Manufacturer Part}') {
				unfixable.push(`${ref}: empty or placeholder value`);
			}

			// Fix floating pins — mark as NC
			const pins = await comp.getAllPins?.();
			if (!pins || !Array.isArray(pins)) continue;

			for (const pin of pins) {
				const pinNum = String(pin.getState_PinNumber?.() || '');
				const pinKey = `${ref}.${pinNum}`;
				const isConnected = connectedPins.has(pinKey);
				const isAlreadyNC = pin.getState_NoConnected?.();

				if (!isConnected && !isAlreadyNC) {
					try {
						pin.setState_NoConnected(true);
						ncCount++;
					}
					catch { /* some pins may not support NC */ }
				}
			}
		}

		if (ncCount > 0) {
			results.push(`Marked ${ncCount} floating pins as No Connect`);
		} else {
			results.push('No floating pins found');
		}

		if (unfixable.length > 0) {
			results.push('\nManual fixes needed:');
			for (const issue of unfixable) {
				results.push('  - ' + issue);
			}
		}

		return results.join('\n');
	}
	catch (e: any) {
		return `DRC fix error: ${e?.message || e}`;
	}
}

// ── Schematic context extraction ──

interface SchematicContext {
	components: ComponentInfo[];
	nets: NetInfo[];
	wires: WireInfo[];
	summary: string;
	rawNetlist: string;
}

interface ComponentInfo {
	ref: string;
	value: string;
	package: string;
	x: number;
	y: number;
	manufacturer?: string;
	lcsc?: string;
}

interface NetInfo {
	name: string;
	pins: string[];
}

interface WireInfo {
	points: string;
}

async function readSchematicContextAsync(): Promise<SchematicContext | null> {
	const components: ComponentInfo[] = [];
	const nets: NetInfo[] = [];
	const errors: string[] = [];

	// Method 1: Component API
	if (typeof eda?.sch_PrimitiveComponent?.getAll === 'function') {
		try {
			const allComponents = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
			if (allComponents && Array.isArray(allComponents)) {
				for (const comp of allComponents) {
					components.push({
						ref: comp.getState_Designator?.() || '?',
						value: comp.getState_Name?.() || '?',
						package: '',
						x: comp.getState_X?.() || 0,
						y: comp.getState_Y?.() || 0,
						manufacturer: comp.getState_Manufacturer?.() || '',
						lcsc: comp.getState_SupplierId?.() || '',
					});
				}
			}
		}
		catch (e: any) {
			errors.push(`Components: ${e?.message || e}`);
		}
	}
	else {
		errors.push('sch_PrimitiveComponent not available');
	}

	// Method 2: Netlist — try multiple formats, capture raw text for AI
	let rawNetlist = '';
	if (typeof eda?.sch_Netlist?.getNetlist === 'function') {
		// Try EasyEDA format first, then default
		const formats = ['EasyEDA', 'JLCEDA', undefined];
		for (const fmt of formats) {
			try {
				const netlistStr = await eda.sch_Netlist.getNetlist(fmt as any);
				if (netlistStr && netlistStr.trim().length > 10) {
					rawNetlist = netlistStr;
					// Parse netlist into structured net data
					const parsed = parseNetlistText(netlistStr);
					nets.push(...parsed);
					break;
				}
			}
			catch { /* try next format */ }
		}
		if (!rawNetlist) {
			errors.push('Netlist: all formats returned empty');
		}
	}

	// Method 2b: Try to get net flags as components
	if (typeof eda?.sch_PrimitiveComponent?.getAll === 'function') {
		try {
			const flags = await eda.sch_PrimitiveComponent.getAll('netFlag' as any, true);
			if (flags && Array.isArray(flags)) {
				for (const flag of flags) {
					const name = flag.getState_Name?.() || flag.getState_Designator?.() || '';
					if (name && !nets.find(n => n.name === name)) {
						nets.push({ name, pins: [] });
					}
				}
			}
		}
		catch { /* netFlag type might not be supported */ }
		try {
			const powers = await eda.sch_PrimitiveComponent.getAll('power' as any, true);
			if (powers && Array.isArray(powers)) {
				for (const p of powers) {
					const name = p.getState_Name?.() || p.getState_Designator?.() || '';
					if (name && !nets.find(n => n.name === name)) {
						nets.push({ name, pins: [] });
					}
				}
			}
		}
		catch { /* power type might not be supported */ }
	}

	// Method 3: Document source fallback
	if (components.length === 0 && typeof eda?.sys_FileManager?.getDocumentSource === 'function') {
		try {
			const source = await eda.sys_FileManager.getDocumentSource();
			if (source) {
				const parsed = parseSourceShapes(source);
				components.push(...parsed.components);
				nets.push(...parsed.nets);
				if (components.length > 0) {
					return {
						components, nets, wires: parsed.wires, rawNetlist: '',
						summary: `Schematic: ${components.length} components, ${nets.length} nets`,
					};
				}
			}
		}
		catch (e: any) {
			errors.push(`DocSource: ${e?.message || e}`);
		}
	}

	if (components.length === 0) {
		if (errors.length > 0) {
			eda.sys_ToastMessage.showMessage(`Debug: ${errors.join(' | ')}`, 'warning' as any);
		}
		return null;
	}

	return {
		components, nets, wires: [], rawNetlist,
		summary: `Schematic: ${components.length} components, ${nets.length} nets`,
	};
}

// ── Netlist parsing — handles multiple EasyEDA Pro netlist formats ──

function parseNetlistText(text: string): NetInfo[] {
	const nets: NetInfo[] = [];

	// Try JSON format first (EasyEDA Pro netlist)
	try {
		const data = JSON.parse(text);
		if (data && typeof data === 'object') {
			const netPins: Record<string, string[]> = {};
			for (const [, compData] of Object.entries(data)) {
				const cd = compData as any;
				if (!cd?.props || !cd?.pins) continue;
				const designator = cd.props.Designator || cd.props.Name || '?';
				for (const [pinNum, netName] of Object.entries(cd.pins)) {
					if (!netName) continue;
					const net = String(netName);
					if (!netPins[net]) netPins[net] = [];
					netPins[net].push(`${designator}.${pinNum}`);
				}
			}
			// Only include named nets (not internal $xxx ones) for the count
			for (const [netName, pins] of Object.entries(netPins)) {
				if (!netName.startsWith('$') || pins.length >= 2) {
					nets.push({ name: netName, pins });
				}
			}
			if (nets.length > 0) return nets;
		}
	}
	catch { /* Not JSON, try text formats */ }

	// Text-based netlist formats
	const lines = text.split('\n');
	let currentNet = '';
	let currentPins: string[] = [];

	for (const line of lines) {
		const trimmed = line.trim();
		if (!trimmed || trimmed.startsWith('*') || trimmed.startsWith('//')) continue;

		const netMatch = trimmed.match(/^(?:\*SIGNAL\*\s+|NET\s+["']?|net\s*\(\s*["']?)([^\s"')]+)/i);
		if (netMatch) {
			if (currentNet) {
				nets.push({ name: currentNet, pins: [...currentPins] });
			}
			currentNet = netMatch[1];
			currentPins = [];
			const rest = trimmed.substring(netMatch[0].length).trim();
			if (rest) {
				const pinRefs = rest.match(/[A-Z_][A-Z0-9_]*\.[A-Z0-9_]+/gi);
				if (pinRefs) currentPins.push(...pinRefs);
			}
			continue;
		}

		if (currentNet) {
			const pinRefs = trimmed.match(/[A-Z_][A-Z0-9_]*\.[A-Z0-9_]+/gi);
			if (pinRefs) {
				currentPins.push(...pinRefs);
			}
			const spacePins = trimmed.match(/([A-Z_][A-Z0-9_]*)\s+(\d+)/gi);
			if (spacePins && !pinRefs) {
				for (const sp of spacePins) {
					currentPins.push(sp.replace(/\s+/, '.'));
				}
			}
		}
	}

	if (currentNet) {
		nets.push({ name: currentNet, pins: [...currentPins] });
	}

	return nets;
}

// ── Shape parsing (fallback for getDocumentSource) ──

function parseSourceShapes(source: string): { components: ComponentInfo[]; nets: NetInfo[]; wires: WireInfo[] } {
	const components: ComponentInfo[] = [];
	const nets: NetInfo[] = [];
	const wires: WireInfo[] = [];
	try {
		const data = JSON.parse(source);
		for (const shape of extractShapes(data)) {
			if (typeof shape !== 'string') continue;
			if (shape.startsWith('LIB~')) { const c = parseLibShape(shape); if (c) components.push(c); }
			else if (shape.startsWith('W~')) { wires.push({ points: shape.substring(2) }); }
			else if (shape.startsWith('F~')) { const n = parseNetFlag(shape); if (n) nets.push(n); }
		}
	}
	catch { /* */ }
	return { components, nets, wires };
}

function extractShapes(data: any): string[] {
	if (!data) return [];
	if (Array.isArray(data)) {
		const r: string[] = [];
		for (const i of data) {
			if (typeof i === 'string') r.push(i);
			else if (typeof i === 'object') r.push(...extractShapes(i));
		}
		return r;
	}
	const s: string[] = [];
	if (data.shape && Array.isArray(data.shape)) s.push(...extractShapes(data.shape));
	if (data.dataStr?.shape) s.push(...extractShapes(data.dataStr.shape));
	if (Array.isArray(data.schematics)) { for (const sc of data.schematics) s.push(...extractShapes(sc)); }
	return s;
}

function parseLibShape(shape: string): ComponentInfo | null {
	try {
		const parts = shape.split('#@$');
		const f = parts[0].split('~');
		let ref = '', value = '', pkg = '';
		for (let i = 1; i < parts.length; i++) {
			const sub = parts[i];
			if (sub.includes('T~P~')) { const t = sub.split('~'); const idx = t.indexOf('comment'); if (idx >= 0) ref = t[idx + 1] || ''; }
			if (sub.includes('T~N~')) { const t = sub.split('~'); const idx = t.indexOf('comment'); if (idx >= 0) value = t[idx + 1] || ''; }
		}
		const m = shape.match(/package`([^`]*)`/i); if (m) pkg = m[1];
		if (!ref && !value) return null;
		return { ref: ref || '?', value: value || '?', package: pkg, x: parseFloat(f[1]) || 0, y: parseFloat(f[2]) || 0 };
	}
	catch { return null; }
}

function parseNetFlag(shape: string): NetInfo | null {
	try {
		const s = shape.split('^^');
		if (s.length >= 3) { const n = s[2].split('~')[0]; if (n) return { name: n, pins: [] }; }
		return null;
	}
	catch { return null; }
}

// ── LCSC mismatch detection ──
// Catches cases where an LCSC number resolves to a completely different component type

const _componentCategories: Record<string, string[]> = {
	'resistor': ['resistor', 'res', 'ohm', 'ω', 'r0', 'r1'],
	'capacitor': ['capacitor', 'cap', 'uf', 'nf', 'pf', 'farad'],
	'ic': ['ic', 'mcu', 'microcontroller', 'atmega', 'stm32', 'esp32', 'max485', 'ds3231'],
	'relay': ['relay', 'ssr', 'g3mb', 'solid state'],
	'transistor': ['transistor', 'mosfet', 'bjt', 'triac', 'bt136', 'irf'],
	'diode': ['diode', 'led', 'zener', '1n4148', '1n4007'],
	'connector': ['connector', 'terminal', 'header', 'pin', 'rj45', 'usb', 'tb-'],
	'optocoupler': ['optocoupler', 'opto', 'moc3', '4n25', 'pc817'],
	'regulator': ['regulator', 'ams1117', 'lm78', 'ldo', '7805'],
};

function _getCategory(text: string): string | null {
	const lower = text.toLowerCase();
	for (const [cat, keywords] of Object.entries(_componentCategories)) {
		for (const kw of keywords) {
			if (lower.includes(kw)) return cat;
		}
	}
	return null;
}

function _isObviousMismatch(partName: string, requestedValue: string): boolean {
	const partCat = _getCategory(partName);
	const reqCat = _getCategory(requestedValue);
	// Only flag mismatch if both resolve to categories AND they differ
	if (partCat && reqCat && partCat !== reqCat) return true;
	return false;
}

// ── Smart placement engine ──
// Tracks occupied positions and places new components intelligently

const GRID_SPACING = 120;  // min distance between components
const _recentPlacements: { x: number; y: number; ref: string }[] = [];

function getSchematicBounds(): { minX: number; minY: number; maxX: number; maxY: number; centerX: number; centerY: number } {
	const ctx = (globalThis as any).__switchboard_context;
	if (!ctx?.components?.length) return { minX: 0, minY: 0, maxX: 1000, maxY: 1000, centerX: 500, centerY: 500 };

	let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
	for (const c of ctx.components) {
		if (c.x || c.y) {
			if (c.x < minX) minX = c.x;
			if (c.x > maxX) maxX = c.x;
			if (c.y < minY) minY = c.y;
			if (c.y > maxY) maxY = c.y;
		}
	}
	if (minX === Infinity) return { minX: 0, minY: 0, maxX: 1000, maxY: 1000, centerX: 500, centerY: 500 };
	return { minX, minY, maxX, maxY, centerX: (minX + maxX) / 2, centerY: (minY + maxY) / 2 };
}

/** Get all occupied positions: existing components + recently placed */
function getOccupiedPositions(): { x: number; y: number; ref: string }[] {
	const positions: { x: number; y: number; ref: string }[] = [];
	const ctx = (globalThis as any).__switchboard_context;
	if (ctx?.components) {
		for (const c of ctx.components) {
			if (c.x || c.y) positions.push({ x: c.x, y: c.y, ref: c.ref });
		}
	}
	positions.push(..._recentPlacements);
	return positions;
}

/** Check if a position is too close to any occupied position */
function isPositionFree(x: number, y: number, occupied: { x: number; y: number }[], minDist: number): boolean {
	for (const p of occupied) {
		if (Math.abs(p.x - x) < minDist && Math.abs(p.y - y) < minDist) return false;
	}
	return true;
}

/**
 * Find components related to a given action by shared net.
 * Uses the raw netlist to find which existing components share nets with the new one.
 */
function findRelatedComponents(action: EditAction): { x: number; y: number; ref: string }[] {
	const ctx = (globalThis as any).__switchboard_context;
	if (!ctx?.rawNetlist) return [];

	try {
		const netlist = JSON.parse(ctx.rawNetlist);
		const relatedRefs = new Set<string>();

		// Find nets that mention the action's ref or value
		const searchTerms = [action.ref, action.value, action.net].filter(Boolean);

		for (const [, compData] of Object.entries(netlist)) {
			const data = compData as any;
			if (!data?.props || !data?.pins) continue;
			const designator = data.props.Designator || '';

			// Get all nets this component is on
			const compNets = new Set(Object.values(data.pins).filter((n: any) => n && !String(n).startsWith('$')));

			// Check if any search term matches this component's nets or designator
			for (const term of searchTerms) {
				if (compNets.has(term) || designator === term) {
					// Find other components on the same named nets
					for (const [, otherData] of Object.entries(netlist)) {
						const other = otherData as any;
						if (!other?.props?.Designator || other.props.Designator === designator) continue;
						const otherNets = new Set(Object.values(other.pins).filter((n: any) => n && !String(n).startsWith('$')));
						for (const net of compNets) {
							if (otherNets.has(net)) relatedRefs.add(other.props.Designator);
						}
					}
				}
			}
		}

		// Map refs to positions
		const occupied = getOccupiedPositions();
		return occupied.filter(p => relatedRefs.has(p.ref));
	}
	catch { return []; }
}

/**
 * Smart position: find the best place for a new component.
 * Priority: 1) Near related components (shared net), 2) Organized grid next to schematic
 */
function findSmartPosition(action: EditAction): { x: number; y: number } {
	const occupied = getOccupiedPositions();
	const bounds = getSchematicBounds();

	// Try to place near related components first
	const related = findRelatedComponents(action);
	if (related.length > 0) {
		// Find the centroid of related components
		const cx = related.reduce((s, p) => s + p.x, 0) / related.length;
		const cy = related.reduce((s, p) => s + p.y, 0) / related.length;

		// Search in a spiral around the centroid for a free spot
		for (let radius = GRID_SPACING; radius < GRID_SPACING * 6; radius += GRID_SPACING) {
			const offsets = [
				{ dx: radius, dy: 0 }, { dx: -radius, dy: 0 },
				{ dx: 0, dy: radius }, { dx: 0, dy: -radius },
				{ dx: radius, dy: radius }, { dx: -radius, dy: radius },
				{ dx: radius, dy: -radius }, { dx: -radius, dy: -radius },
			];
			for (const off of offsets) {
				const tx = Math.round(cx + off.dx);
				const ty = Math.round(cy + off.dy);
				if (isPositionFree(tx, ty, occupied, GRID_SPACING * 0.8)) {
					return { x: tx, y: ty };
				}
			}
		}
	}

	// Fallback: organized column to the right of the schematic
	const startX = bounds.maxX + GRID_SPACING * 2;
	const startY = bounds.minY;
	const colWidth = GRID_SPACING * 2;
	const maxRows = 10;

	for (let col = 0; col < 5; col++) {
		for (let row = 0; row < maxRows; row++) {
			const tx = startX + col * colWidth;
			const ty = startY + row * GRID_SPACING;
			if (isPositionFree(tx, ty, occupied, GRID_SPACING * 0.8)) {
				return { x: tx, y: ty };
			}
		}
	}

	// Last resort
	return { x: bounds.maxX + GRID_SPACING * 2, y: bounds.centerY };
}

/**
 * Remap AI coordinates to real schematic coordinates.
 * If AI gives 0,0 or small coords → use smart placement.
 * If already in schematic range → keep as-is.
 */
function remapCoord(x: number, y: number, action?: EditAction): { x: number; y: number } {
	const bounds = getSchematicBounds();

	// If AI gave 0,0 or no meaningful coords, use smart placement
	// Check this FIRST — before bounds check, since 0,0 can fall within schematic bounds
	if ((!x && !y) || (x < 10 && y < 10)) {
		return findSmartPosition(action || {} as EditAction);
	}

	// If coordinates are already in the schematic's range, keep them
	if (x >= bounds.minX - 200 && x <= bounds.maxX + 200 && y >= bounds.minY - 200 && y <= bounds.maxY + 200) {
		return { x, y };
	}

	// AI uses ~0-1200 range: map to schematic extent
	const spanX = (bounds.maxX - bounds.minX) || 400;
	const spanY = (bounds.maxY - bounds.minY) || 400;
	const margin = 100;
	const mappedX = bounds.minX - margin + (x / 1200) * (spanX + 2 * margin);
	const mappedY = bounds.minY - margin + (y / 1200) * (spanY + 2 * margin);
	return { x: Math.round(mappedX), y: Math.round(mappedY) };
}

/** Register a placement so future placements avoid it */
function registerPlacement(x: number, y: number, ref: string): void {
	_recentPlacements.push({ x, y, ref });
	// Keep only recent placements (avoid unbounded growth)
	if (_recentPlacements.length > 50) _recentPlacements.shift();
}

// ── Write-back: Apply AI edits to the EasyEDA schematic ──

interface EditAction {
	op: string;
	[key: string]: any;
}

interface EditResult {
	ok: boolean;
	msg: string;
	error?: string;
}

/**
 * Apply a batch of edit actions to the live schematic.
 * Called from the iframe via globalThis.__switchboard_applyEdits
 */
async function applyEdits(actions: EditAction[]): Promise<EditResult[]> {
	const results: EditResult[] = [];

	// Pre-fetch live component refs for validation
	let liveRefs: Set<string> | null = null;
	try {
		const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
		if (allComps && Array.isArray(allComps)) {
			liveRefs = new Set<string>();
			for (const c of allComps) {
				const ref = c.getState_Designator?.();
				if (ref) liveRefs.add(ref);
			}
		}
	}
	catch { /* proceed without pre-validation */ }

	for (const action of actions) {
		try {
			// Pre-apply: verify ref exists on live canvas for mutating ops
			if (liveRefs) {
				const ref = action.ref;
				const mutatingOps = ['modify_component', 'move_component', 'delete_component', 'remove_component', 'replace_component'];
				if (ref && mutatingOps.includes(action.op) && !liveRefs.has(ref)) {
					results.push({ ok: false, msg: '', error: `"${ref}" not found on EasyEDA canvas` });
					continue;
				}
			}

			const result = await applyOneEdit(action);
			results.push(result);

			// Track additions so later actions in the same batch can reference them
			if (action.op === 'add_component' && action.ref && liveRefs) {
				liveRefs.add(action.ref);
			}
		}
		catch (e: any) {
			results.push({ ok: false, msg: '', error: e?.message || String(e) });
		}
	}

	return results;
}

async function applyOneEdit(action: EditAction): Promise<EditResult> {
	switch (action.op) {
		case 'add_net_flag':
			return await addNetFlag(action);
		case 'add_net_label':
			return await addNetLabel(action);
		case 'add_wire':
			return await addWire(action);
		case 'add_component':
			return await addComponent(action);
		case 'modify_component':
			return await modifyComponent(action);
		case 'delete_component':
			return await deleteComponent(action);
		case 'add_text':
			return await addText(action);
		case 'place_component':
			return await placeComponent(action);
		case 'move_component':
			return await moveComponent(action);
		case 'add_subcircuit':
			return await addSubcircuit(action);
		case 'remove_component':
			return await deleteComponent(action);
		case 'replace_component':
			return await replaceComponent(action);
		case 'connect_pin':
			return await connectPin(action);
		case 'set_no_connect':
			return await setNoConnect(action);
		case 'run_drc':
			return await runDrc();
		case 'remove_wire':
		case 'remove_net_label':
			return { ok: true, msg: `${action.op} — backend only` };
		default:
			return { ok: false, msg: '', error: `Unknown op: ${action.op}` };
	}
}

/**
 * Add a net label as text on the schematic.
 * Uses sch_PrimitiveText.create() which is the most reliable — no internal HTTP calls.
 * action: { op: 'add_net_label', name: string, x: number, y: number }
 */
async function addNetLabel(action: EditAction): Promise<EditResult> {
	const name = action.name || action.net || '';
	if (!name) return { ok: false, msg: '', error: 'No net name' };

	let pos: { x: number; y: number };

	// If near_ref is specified, place the label near that component
	if (action.near_ref && _recentPlacements.length > 0) {
		const nearby = _recentPlacements.find(p => p.ref === action.near_ref);
		if (nearby) {
			pos = { x: nearby.x + 60, y: nearby.y - 30 };
		}
		else {
			pos = remapCoord(action.x || 0, action.y || 0, action);
		}
	}
	else {
		pos = remapCoord(action.x || 0, action.y || 0, action);
	}

	const x = pos.x;
	const y = pos.y;

	try {
		const result = await eda.sch_PrimitiveText.create(x, y, name);
		if (result) return { ok: true, msg: `Added net label "${name}"` };
	}
	catch { /* */ }

	return { ok: false, msg: '', error: `Could not create net label "${name}"` };
}

/**
 * Add a component by looking up its LCSC number in EasyEDA's library,
 * then attaching it to the mouse for placement.
 * action: { op: 'add_component', ref: string, value: string, package?: string, lcsc?: string }
 */
async function addComponent(action: EditAction): Promise<EditResult> {
	const ref = action.ref || '?';
	const value = action.value || '?';
	const lcsc = action.lcsc || '';
	const connections: Record<string, string> = action.connections || {};

	// Smart placement: use AI coords if meaningful, otherwise auto-position
	const pos = remapCoord(action.x || 0, action.y || 0, action);

	let placedComp: any = null;
	let placedVia = '';

	// Try to find and place the component by LCSC number
	if (!placedComp && lcsc && typeof eda?.lib_Device?.getByLcscIds === 'function') {
		try {
			const device = await eda.lib_Device.getByLcscIds(lcsc);
			const item = Array.isArray(device) ? device[0] : device;
			if (item && item.uuid && item.libraryUuid) {
				const partName = (item.name || item.title || '').toLowerCase();
				const valLower = value.toLowerCase();
				const isMismatch = partName && valLower &&
					!partName.includes(valLower.replace(/[ωΩ]/g, '')) &&
					!valLower.includes(partName.split(' ')[0]) &&
					_isObviousMismatch(partName, valLower);
				if (isMismatch) {
					console.warn(`LCSC ${lcsc} returned "${partName}" but expected "${value}" — skipping, will search by name`);
				}
				else {
					placedComp = await eda.sch_PrimitiveComponent.create(
						{ libraryUuid: item.libraryUuid, uuid: item.uuid },
						pos.x, pos.y,
					);
					if (placedComp) placedVia = `[${lcsc}]`;
				}
			}
		}
		catch { /* LCSC lookup failed, try search */ }
	}

	// Fallback: search by component name/value
	if (!placedComp && typeof eda?.lib_Device?.search === 'function') {
		try {
			const searchTerm = value || ref;
			const results = await eda.lib_Device.search(searchTerm);
			if (results && results.length > 0) {
				const item = results[0];
				if (item.uuid && item.libraryUuid) {
					placedComp = await eda.sch_PrimitiveComponent.create(
						{ libraryUuid: item.libraryUuid, uuid: item.uuid },
						pos.x, pos.y,
					);
					if (placedComp) placedVia = 'via search';
				}
			}
		}
		catch { /* search failed */ }
	}

	// If placed successfully, register + auto-connect pins
	if (placedComp) {
		registerPlacement(pos.x, pos.y, ref);
		const connMsgs = await _autoConnectPins(placedComp, connections, ref);
		const connSummary = connMsgs.length > 0 ? ` → connected: ${connMsgs.join(', ')}` : '';
		return { ok: true, msg: `Placed ${ref} (${value}) ${placedVia} at (${pos.x}, ${pos.y})${connSummary}` };
	}

	// Last resort: add as text annotation
	try {
		await eda.sch_PrimitiveText.create(pos.x, pos.y, `[${ref}: ${value}]`);
		registerPlacement(pos.x, pos.y, ref);
		return { ok: true, msg: `${ref} (${value}) — as text (not found in library)` };
	}
	catch { /* */ }

	return { ok: false, msg: '', error: `Could not place ${ref} (${value})` };
}

/**
 * Auto-connect pins after placing a component.
 * connections: { pinNumber: netName } — e.g., { "1": "VCC", "4": "GND", "6": "RS485_A" }
 * Places net flags (Power/Ground) or net ports at exact pin positions.
 */
async function _autoConnectPins(comp: any, connections: Record<string, string>, ref: string): Promise<string[]> {
	if (!connections || Object.keys(connections).length === 0) return [];

	const msgs: string[] = [];
	try {
		const pins = await comp.getAllPins?.();
		if (!pins || !Array.isArray(pins)) return [];

		for (const [pinNum, netName] of Object.entries(connections)) {
			// Find pin by number (or by name as fallback)
			const pin = pins.find((p: any) =>
				String(p.getState_PinNumber?.()) === String(pinNum) ||
				String(p.getState_PinName?.()).toUpperCase() === String(pinNum).toUpperCase()
			);
			if (!pin) {
				msgs.push(`pin ${pinNum} not found`);
				continue;
			}

			const px = pin.getState_X?.();
			const py = pin.getState_Y?.();
			if (px == null || py == null) continue;

			const connected = await _placeNetAtPin(netName, px, py);
			if (connected) {
				msgs.push(`${pinNum}→${netName}`);
			}
			else {
				msgs.push(`${pinNum}→${netName} (text)`);
			}
		}
	}
	catch (e: any) {
		msgs.push(`pin connect error: ${e?.message || e}`);
	}
	return msgs;
}

/**
 * Place the appropriate net symbol at a pin position.
 * - GND/AGND/DGND → Ground flag
 * - VCC/5V/3.3V/12V etc → Power flag
 * - Everything else → Net port (bidirectional)
 */
async function _placeNetAtPin(netName: string, x: number, y: number): Promise<boolean> {
	const upper = netName.toUpperCase();
	const isGround = /^(GND|AGND|DGND|VSS|GROUND|0V)$/i.test(netName);
	const isPower = /^(VCC|VDD|5V|3\.3V|3V3|12V|24V|VBAT|VBUS|V\+|5V[-_]OUTPUT)$/i.test(netName);

	try {
		if (isGround) {
			const result = await eda.sch_PrimitiveComponent.createNetFlag(
				'Ground' as any, netName, x, y, 0,
			);
			if (result) return true;
		}
		else if (isPower) {
			const result = await eda.sch_PrimitiveComponent.createNetFlag(
				'Power' as any, netName, x, y, 0,
			);
			if (result) return true;
		}

		// For signal nets, use net port (bidirectional)
		if (typeof eda?.sch_PrimitiveComponent?.createNetPort === 'function') {
			const result = await eda.sch_PrimitiveComponent.createNetPort(
				'BI' as any, netName, x, y, 0, false,
			);
			if (result) return true;
		}

		// Last fallback: text label at pin
		await eda.sch_PrimitiveText.create(x, y, netName);
		return true;
	}
	catch {
		// Text fallback
		try { await eda.sch_PrimitiveText.create(x, y, netName); return true; }
		catch { return false; }
	}
}

/**
 * Connect a pin on an existing component to a net.
 * action: { op: "connect_pin", ref: "U11", pin: "1", net: "VCC" }
 */
async function connectPin(action: EditAction): Promise<EditResult> {
	const ref = action.ref || '';
	const pinId = String(action.pin || '');
	const netName = action.net || action.name || '';

	if (!ref || !pinId || !netName) {
		return { ok: false, msg: '', error: 'connect_pin requires ref, pin, and net' };
	}

	// Find the component
	try {
		const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
		if (!allComps) return { ok: false, msg: '', error: `Cannot access components` };

		for (const comp of allComps) {
			const designator = comp.getState_Designator?.() || '';
			if (designator !== ref) continue;

			const pins = await comp.getAllPins?.();
			if (!pins || !Array.isArray(pins)) {
				return { ok: false, msg: '', error: `${ref} has no accessible pins` };
			}

			const pin = pins.find((p: any) =>
				String(p.getState_PinNumber?.()) === pinId ||
				String(p.getState_PinName?.()).toUpperCase() === pinId.toUpperCase()
			);
			if (!pin) {
				const available = pins.map((p: any) =>
					`${p.getState_PinNumber?.()}(${p.getState_PinName?.() || '?'})`
				).join(', ');
				return { ok: false, msg: '', error: `Pin "${pinId}" not found on ${ref}. Available: ${available}` };
			}

			const px = pin.getState_X?.();
			const py = pin.getState_Y?.();
			if (px == null || py == null) {
				return { ok: false, msg: '', error: `Cannot read position of pin ${pinId} on ${ref}` };
			}

			const connected = await _placeNetAtPin(netName, px, py);
			if (connected) {
				return { ok: true, msg: `Connected ${ref} pin ${pinId} → ${netName} at (${px}, ${py})` };
			}
			return { ok: false, msg: '', error: `Failed to place net at pin ${pinId} on ${ref}` };
		}

		return { ok: false, msg: '', error: `Component ${ref} not found on schematic` };
	}
	catch (e: any) {
		return { ok: false, msg: '', error: `connect_pin error: ${e?.message || e}` };
	}
}

/**
 * Set No Connect flag on specific pins of a component.
 * action: { op: "set_no_connect", ref: "BRAIN", pins: ["5", "6", "21"] }
 * If pins is omitted, marks ALL unconnected pins as NC.
 */
async function setNoConnect(action: EditAction): Promise<EditResult> {
	const ref = action.ref || '';
	const pinList: string[] = action.pins || [];

	if (!ref) return { ok: false, msg: '', error: 'set_no_connect requires ref' };

	try {
		const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
		if (!allComps) return { ok: false, msg: '', error: 'Cannot access components' };

		for (const comp of allComps) {
			if ((comp.getState_Designator?.() || '') !== ref) continue;

			const pins = await comp.getAllPins?.();
			if (!pins || !Array.isArray(pins)) {
				return { ok: false, msg: '', error: `${ref} has no accessible pins` };
			}

			const marked: string[] = [];
			for (const pin of pins) {
				const num = String(pin.getState_PinNumber?.() || '');
				const name = pin.getState_PinName?.() || '';

				// If specific pins listed, only mark those. Otherwise mark all.
				if (pinList.length > 0 && !pinList.includes(num) && !pinList.includes(name)) {
					continue;
				}

				// Skip if already marked NC
				if (pin.getState_NoConnected?.()) continue;

				try {
					pin.setState_NoConnected(true);
					marked.push(`${num}(${name})`);
				}
				catch { /* some pins may not support it */ }
			}

			if (marked.length > 0) {
				return { ok: true, msg: `Marked ${marked.length} pins NC on ${ref}: ${marked.join(', ')}` };
			}
			return { ok: true, msg: `No pins needed NC marking on ${ref}` };
		}

		return { ok: false, msg: '', error: `Component ${ref} not found` };
	}
	catch (e: any) {
		return { ok: false, msg: '', error: `set_no_connect error: ${e?.message || e}` };
	}
}

/**
 * Run schematic DRC and return errors/warnings.
 * action: { op: "run_drc" }
 */
async function runDrc(): Promise<EditResult> {
	try {
		if (typeof eda?.sch_Drc?.check !== 'function') {
			return { ok: false, msg: '', error: 'DRC API not available' };
		}

		const errors = await eda.sch_Drc.check(true, false, true);
		if (!errors || (Array.isArray(errors) && errors.length === 0)) {
			return { ok: true, msg: 'DRC passed — no errors found' };
		}

		if (typeof errors === 'boolean') {
			return { ok: true, msg: errors ? 'DRC passed' : 'DRC found issues (run with UI for details)' };
		}

		// Parse DRC results
		const issues: string[] = [];
		for (const err of (errors as any[])) {
			const msg = err?.message || err?.msg || err?.description || JSON.stringify(err);
			issues.push(String(msg));
		}

		return {
			ok: true,
			msg: `DRC found ${issues.length} issues:\n${issues.slice(0, 20).join('\n')}`,
		};
	}
	catch (e: any) {
		return { ok: false, msg: '', error: `DRC error: ${e?.message || e}` };
	}
}

/**
 * Add a power/ground net flag.
 * action: { op: 'add_net_flag', identification: 'Power'|'Ground', net: string, x: number, y: number, rotation?: number }
 */
async function addNetFlag(action: EditAction): Promise<EditResult> {
	const identification = action.identification || 'Power';
	const net = action.net || 'VCC';
	const pos = remapCoord(action.x || 0, action.y || 0);
	const x = pos.x;
	const y = pos.y;
	const rotation = action.rotation || 0;

	// Try the real net flag API first
	try {
		const result = await eda.sch_PrimitiveComponent.createNetFlag(
			identification as any,
			net,
			x, y,
			rotation,
		);
		if (result) {
			return { ok: true, msg: `Added ${identification} net flag "${net}" at (${x}, ${y})` };
		}
	}
	catch { /* createNetFlag can 404 internally — fall back to text */ }

	// Fallback: place as text label
	try {
		const result = await eda.sch_PrimitiveText.create(x, y, net);
		if (result) return { ok: true, msg: `Added "${net}" as text label (flag unavailable)` };
	}
	catch { /* */ }

	return { ok: false, msg: '', error: `Failed to create net flag "${net}"` };
}

/**
 * Add a wire between points.
 * action: { op: 'add_wire', points: [{x,y},{x,y},...] OR [x1,y1,x2,y2,...], net?: string }
 */
async function addWire(action: EditAction): Promise<EditResult> {
	let rawPoints = action.points || [];
	const net = action.net || undefined;

	// Convert [{x,y},{x,y}] format to flat [x1,y1,x2,y2] and remap coordinates
	let flatPoints: number[];
	if (rawPoints.length > 0 && typeof rawPoints[0] === 'object' && 'x' in rawPoints[0]) {
		flatPoints = [];
		for (const p of rawPoints) {
			const mapped = remapCoord(p.x, p.y);
			flatPoints.push(mapped.x, mapped.y);
		}
	}
	else {
		// Flat array — remap pairs
		flatPoints = [];
		for (let i = 0; i < rawPoints.length; i += 2) {
			const mapped = remapCoord(rawPoints[i], rawPoints[i + 1]);
			flatPoints.push(mapped.x, mapped.y);
		}
	}

	if (flatPoints.length < 4) {
		return { ok: false, msg: '', error: 'Wire needs at least 2 points (4 coordinates)' };
	}

	try {
		const result = await eda.sch_PrimitiveWire.create(flatPoints, net);
		if (result) {
			return { ok: true, msg: `Added wire with ${flatPoints.length / 2} points${net ? ` on net "${net}"` : ''}` };
		}
	}
	catch { /* wire creation can fail for various reasons */ }

	return { ok: false, msg: '', error: 'Failed to create wire' };
}

/**
 * Modify an existing component's properties.
 * action: { op: 'modify_component', ref: string, designator?: string, name?: string, ... }
 */
async function modifyComponent(action: EditAction): Promise<EditResult> {
	const ref = action.ref;
	if (!ref) return { ok: false, msg: '', error: 'No ref provided' };

	// Find the component by designator
	const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
	const target = allComps?.find((c: any) => c.getState_Designator?.() === ref);
	if (!target) {
		return { ok: false, msg: '', error: `Component "${ref}" not found` };
	}

	const props: any = {};
	if (action.designator !== undefined) props.designator = action.designator;
	// AI sends "value" but EasyEDA calls it "name"
	if (action.value !== undefined) props.name = action.value;
	if (action.name !== undefined) props.name = action.name;
	if (action.x !== undefined) {
		const pos = remapCoord(action.x, action.y || 0);
		props.x = pos.x;
		props.y = pos.y;
	}
	else if (action.y !== undefined) {
		props.y = action.y;
	}
	if (action.rotation !== undefined) props.rotation = action.rotation;
	if (action.manufacturer !== undefined) props.manufacturer = action.manufacturer;
	if (action.supplierId !== undefined) props.supplierId = action.supplierId;
	// Map lcsc to supplierId
	if (action.lcsc !== undefined) props.supplierId = action.lcsc;

	try {
		const result = await eda.sch_PrimitiveComponent.modify(target as any, props);
		if (result) {
			return { ok: true, msg: `Modified ${ref}` };
		}
	}
	catch { /* */ }
	return { ok: false, msg: '', error: `Failed to modify "${ref}"` };
}

/**
 * Delete a component by reference designator.
 * action: { op: 'delete_component', ref: string }
 */
async function deleteComponent(action: EditAction): Promise<EditResult> {
	const ref = action.ref;
	if (!ref) return { ok: false, msg: '', error: 'No ref provided' };

	const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
	const target = allComps?.find((c: any) => c.getState_Designator?.() === ref);
	if (!target) {
		return { ok: false, msg: '', error: `Component "${ref}" not found` };
	}

	const result = await eda.sch_PrimitiveComponent.delete(target as any);
	if (result) {
		return { ok: true, msg: `Deleted ${ref}` };
	}
	return { ok: false, msg: '', error: `Failed to delete "${ref}"` };
}

/**
 * Replace a component: remove old, place new at the exact same position.
 * Net flags/labels preserve connectivity automatically.
 * action: { op: 'replace_component', ref: string, value: string, lcsc?: string, package?: string }
 */
async function replaceComponent(action: EditAction): Promise<EditResult> {
	const ref = action.ref;
	if (!ref) return { ok: false, msg: '', error: 'No ref provided' };

	const newValue = action.value || action.new_value || '?';
	const newLcsc = action.lcsc || action.new_lcsc || '';

	// Find the old component and read its position/rotation
	const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
	const target = allComps?.find((c: any) => c.getState_Designator?.() === ref);
	if (!target) {
		return { ok: false, msg: '', error: `Component "${ref}" not found` };
	}

	const oldX = target.getState_X?.() || 0;
	const oldY = target.getState_Y?.() || 0;
	const oldRotation = target.getState_Rotation?.() || 0;

	// Remove old component
	try {
		await eda.sch_PrimitiveComponent.delete(target as any);
	}
	catch {
		return { ok: false, msg: '', error: `Failed to remove old "${ref}"` };
	}

	// Place new component at the exact same position
	if (newLcsc && typeof eda?.lib_Device?.getByLcscIds === 'function') {
		try {
			const device = await eda.lib_Device.getByLcscIds(newLcsc);
			const item = Array.isArray(device) ? device[0] : device;
			if (item && item.uuid && item.libraryUuid) {
				const comp = await eda.sch_PrimitiveComponent.create(
					{ libraryUuid: item.libraryUuid, uuid: item.uuid },
					oldX, oldY,
				);
				if (comp) {
					// Restore rotation
					if (oldRotation) {
						try { await eda.sch_PrimitiveComponent.modify(comp as any, { rotation: oldRotation } as any); }
						catch { /* rotation is best-effort */ }
					}
					return { ok: true, msg: `Replaced ${ref} → ${newValue} [${newLcsc}] at (${oldX}, ${oldY})` };
				}
			}
		}
		catch { /* LCSC lookup failed, try search */ }
	}

	// Fallback: search by name
	if (typeof eda?.lib_Device?.search === 'function') {
		try {
			const results = await eda.lib_Device.search(newLcsc || newValue);
			if (results && results.length > 0) {
				const item = results[0];
				if (item.uuid && item.libraryUuid) {
					const comp = await eda.sch_PrimitiveComponent.create(
						{ libraryUuid: item.libraryUuid, uuid: item.uuid },
						oldX, oldY,
					);
					if (comp) {
						if (oldRotation) {
							try { await eda.sch_PrimitiveComponent.modify(comp as any, { rotation: oldRotation } as any); }
							catch { /* */ }
						}
						return { ok: true, msg: `Replaced ${ref} → ${newValue} via search at (${oldX}, ${oldY})` };
					}
				}
			}
		}
		catch { /* search failed */ }
	}

	// Last resort: text annotation at same position
	try {
		await eda.sch_PrimitiveText.create(oldX, oldY, `[${ref}: ${newValue}]`);
		return { ok: true, msg: `${ref} removed, ${newValue} as text (LCSC not found)` };
	}
	catch { /* */ }

	return { ok: false, msg: '', error: `Removed ${ref} but could not place replacement ${newValue}` };
}

/**
 * Add a text label to the schematic.
 * action: { op: 'add_text', x: number, y: number, content: string, rotation?: number }
 */
async function addText(action: EditAction): Promise<EditResult> {
	const pos = remapCoord(action.x || 0, action.y || 0);
	const result = await eda.sch_PrimitiveText.create(
		pos.x,
		pos.y,
		action.content || '',
		action.rotation || 0,
	);
	if (result) {
		return { ok: true, msg: `Added text "${action.content}"` };
	}
	return { ok: false, msg: '', error: 'Failed to add text' };
}

/**
 * Place a component from the EasyEDA library using mouse placement.
 * action: { op: 'place_component', libraryUuid: string, uuid: string }
 * The component attaches to the mouse cursor for the user to click-place.
 */
async function placeComponent(action: EditAction): Promise<EditResult> {
	if (!action.libraryUuid || !action.uuid) {
		return { ok: false, msg: '', error: 'libraryUuid and uuid required' };
	}

	const result = await eda.sch_PrimitiveComponent.placeComponentWithMouse(
		{ libraryUuid: action.libraryUuid, uuid: action.uuid },
		action.subPartName,
	);

	if (result) {
		return { ok: true, msg: `Component attached to mouse — click to place` };
	}
	return { ok: false, msg: '', error: 'Component not found in library' };
}

/**
 * Move a component to new coordinates.
 * action: { op: 'move_component', ref: string, x: number, y: number }
 */
async function moveComponent(action: EditAction): Promise<EditResult> {
	const ref = action.ref;
	if (!ref) return { ok: false, msg: '', error: 'No ref provided' };

	const allComps = await eda.sch_PrimitiveComponent.getAll('part' as any, true);
	const target = allComps?.find((c: any) => c.getState_Designator?.() === ref);
	if (!target) {
		return { ok: false, msg: '', error: `Component "${ref}" not found` };
	}

	const pos = remapCoord(action.x || 0, action.y || 0);
	try {
		const result = await eda.sch_PrimitiveComponent.modify(target as any, { x: pos.x, y: pos.y } as any);
		if (result) return { ok: true, msg: `Moved ${ref} to (${pos.x}, ${pos.y})` };
	}
	catch { /* */ }

	return { ok: false, msg: '', error: `Failed to move "${ref}"` };
}

/**
 * Add a subcircuit by expanding its template into individual add_component actions.
 * action: { op: 'add_subcircuit', subcircuit_id: string }
 */
async function addSubcircuit(action: EditAction): Promise<EditResult> {
	// Subcircuit templates — same as backend knowledge_base.py
	const templates: Record<string, Array<{ ref: string; value: string; lcsc: string }>> = {
		rs485_transceiver: [
			{ ref: 'U_RS', value: 'MAX485ESA+', lcsc: 'C6855' },
			{ ref: 'R_TERM', value: '120R', lcsc: 'C17437' },
			{ ref: 'R_PU', value: '10K', lcsc: 'C17414' },
			{ ref: 'R_PD', value: '10K', lcsc: 'C17414' },
			{ ref: 'C_DEC', value: '100nF', lcsc: 'C49678' },
		],
		rtc_ds3231: [
			{ ref: 'U_RTC', value: 'DS3231MZ+', lcsc: 'C9865' },
			{ ref: 'R_SDA', value: '4.7K', lcsc: 'C17673' },
			{ ref: 'R_SCL', value: '4.7K', lcsc: 'C17673' },
			{ ref: 'C_RTC', value: '100nF', lcsc: 'C49678' },
		],
		sd_card_logger: [
			{ ref: 'U_REG', value: 'AMS1117-3.3', lcsc: 'C6186' },
			{ ref: 'C_IN', value: '10uF', lcsc: 'C15850' },
			{ ref: 'C_OUT', value: '22uF', lcsc: 'C159842' },
			{ ref: 'C_SD', value: '100nF', lcsc: 'C49678' },
		],
		triac_driver: [
			{ ref: 'U_OPTO', value: 'MOC3021M', lcsc: 'C89255' },
			{ ref: 'Q_TR', value: 'BT136-800E', lcsc: 'C73484' },
			{ ref: 'R_GATE', value: '360R', lcsc: 'C17694' },
			{ ref: 'R_LED', value: '470R', lcsc: 'C17712' },
			{ ref: 'R_SNUB', value: '39R', lcsc: 'C25207' },
			{ ref: 'C_SNUB', value: '100nF/400V', lcsc: 'C123821' },
		],
	};

	const scId = action.subcircuit_id || '';
	const template = templates[scId];
	if (!template) {
		return { ok: false, msg: '', error: `Unknown subcircuit: ${scId}` };
	}

	const placed: string[] = [];
	const failed: string[] = [];

	for (let i = 0; i < template.length; i++) {
		const comp = template[i];

		// Let smart placement handle positioning (pass 0,0 to trigger auto-position)
		const result = await addComponent({
			op: 'add_component',
			ref: comp.ref,
			value: comp.value,
			lcsc: comp.lcsc,
			x: 0, y: 0,
		});
		if (result.ok) placed.push(comp.ref);
		else failed.push(comp.ref);
	}

	return {
		ok: true,
		msg: `Subcircuit ${scId}: placed ${placed.length}/${template.length} (${placed.join(', ')})`,
	};
}
