/**
 * Copy the built .sdPlugin bundle into the repo sd_plugin folder used by
 * Mycelian.iss (Source: "sd_plugin\*").
 */
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(scriptDir, "..");
const src = join(pluginRoot, "com.mushroomsuprise.mycelian.sdPlugin");
const dest = join(pluginRoot, "..", "..", "sd_plugin", "com.mushroomsuprise.mycelian.sdPlugin");

if (!existsSync(src)) {
	console.error(`Stream Deck build output not found: ${src}`);
	process.exit(1);
}

mkdirSync(dirname(dest), { recursive: true });
cpSync(src, dest, { recursive: true, force: true });
console.log(`Deployed Stream Deck plugin to ${dest}`);
