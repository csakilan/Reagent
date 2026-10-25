"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import Editor from "@monaco-editor/react";
import { exampleAgentYaml } from "../../../../fixtures/tab-examples";

export default function EditorPage() {
  const params = useParams<{ name: string }>(); const name = decodeURIComponent(params.name);
  const [yaml, setYaml] = useState(exampleAgentYaml.replace("pr-reviewer", name)); const [valid, setValid] = useState<boolean | null>(true);
  function validateLocally() { setValid(yaml.includes("name:") && yaml.includes("model:") && yaml.includes("guardrails:")); }
  return <div className="shell"><aside className="rail"><div className="brand">ReAgent<small>POLICY CONTROL PLANE</small></div><nav className="nav"><Link href="/">Resolved view</Link><Link href="/traces">Trace viewer</Link><Link href="/publish">Publish pipeline</Link><Link className="active" href="/agents">Collection</Link><Link href="/approvals">Approvals</Link></nav></aside><main><header className="page-head"><div><div className="eyebrow"><Link href="/agents">Collection</Link> / Editor</div><h1>{name}</h1><p className="subtitle">Example agent configuration · local validation preview</p></div><button className="button primary" onClick={validateLocally}>Validate locally</button></header><div className="example-banner">EXAMPLE DATA · LOCAL PREVIEW ONLY · Edits stay in this page and are never saved or sent to the runtime.</div><div className="editor-frame"><Editor height="62vh" language="yaml" theme="reagent" beforeMount={(monaco) => { const style = getComputedStyle(document.documentElement); const background = style.getPropertyValue("--background").trim(); const ink = style.getPropertyValue("--ink").trim(); monaco.editor.defineTheme("reagent", { base: "vs", inherit: true, rules: [], colors: { "editor.background": background, "editor.foreground": ink, "editorLineNumber.foreground": style.getPropertyValue("--muted").trim(), "editorGutter.background": background } }); }} value={yaml} onChange={value => { setYaml(value ?? ""); setValid(null); }} options={{ minimap: { enabled: false }, lineNumbers: "on", fontFamily: "IBM Plex Mono", fontSize: 13, scrollBeyondLastLine: false, automaticLayout: true }} /></div>{valid !== null && <div className={`validation ${valid ? "validation-ok" : "validation-error"}`}>{valid ? "Example configuration includes name, model and guardrail fields." : "Example validation failed: add name, model and guardrails fields."}</div>}</main></div>;
}
