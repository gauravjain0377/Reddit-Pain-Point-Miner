import { useState } from "react";
import { Copy, Download, Link2, Check } from "lucide-react";

/**
 * ExportBar.jsx — three export actions in a slim toolbar.
 *
 * Copy as Markdown: formats the report into a shareable .md document
 *   and uses the Clipboard API to copy it. Ideal for pasting into Notion
 *   or a Slack message.
 *
 * Download JSON: creates an object URL from the raw report dict and
 *   programmatically clicks an <a> to trigger the browser's save dialog.
 *   No server round-trip needed.
 *
 * Share Link: copies window.location.href to clipboard — works because
 *   the backend caches results, so the /results/{jobId} URL is stable.
 *
 * All three buttons show a transient ✓ checkmark on success before
 * reverting — standard UX pattern for clipboard actions.
 */

function useFlash() {
  const [flashed, setFlashed] = useState(false);
  function flash() {
    setFlashed(true);
    setTimeout(() => setFlashed(false), 2000);
  }
  return [flashed, flash];
}

function toMarkdown(report) {
  if (!report) return "";
  const lines = [
    `# ${report.niche} — Reddit Pain Point Analysis`,
    "",
    "## Executive Summary",
    "",
    report.summary ?? "",
    "",
    "## Top Pain Points",
    "",
  ];

  (report.top_pain_points ?? []).forEach((pp) => {
    lines.push(`### ${pp.rank}. ${pp.pain_text}`);
    lines.push(`- **Category:** ${pp.category}`);
    lines.push(`- **Severity:** ${pp.severity}/10`);
    lines.push(`- **Mentions:** ${pp.mention_count}`);
    lines.push(`- **Confidence:** ${Math.round((pp.confidence ?? 0) * 100)}%`);
    lines.push(`- **Quote:** _"${pp.verbatim_quote}"_`);
    if (pp.source_url) lines.push(`- [Source](${pp.source_url})`);
    lines.push("");
  });

  lines.push("## Categories Breakdown", "");
  Object.entries(report.categories_breakdown ?? {}).forEach(([cat, count]) => {
    lines.push(`- **${cat}:** ${count}`);
  });

  return lines.join("\n");
}

export default function ExportBar({ report }) {
  const [mdFlash, flashMd] = useFlash();
  const [jsonFlash, flashJson] = useFlash();
  const [linkFlash, flashLink] = useFlash();

  function copyMarkdown() {
    const md = toMarkdown(report);
    navigator.clipboard.writeText(md).then(flashMd).catch(console.error);
  }

  function downloadJson() {
    const blob = new Blob(
      [JSON.stringify(report, null, 2)],
      { type: "application/json" }
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(report?.niche ?? "report").replace(/\s+/g, "_")}_painpoints.json`;
    a.click();
    URL.revokeObjectURL(url);
    flashJson();
  }

  function copyLink() {
    navigator.clipboard
      .writeText(window.location.href)
      .then(flashLink)
      .catch(console.error);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <ExportButton
        onClick={copyMarkdown}
        icon={<Copy className="w-3.5 h-3.5" />}
        label="Copy as Markdown"
        flashed={mdFlash}
      />
      <ExportButton
        onClick={downloadJson}
        icon={<Download className="w-3.5 h-3.5" />}
        label="Download JSON"
        flashed={jsonFlash}
      />
      <ExportButton
        onClick={copyLink}
        icon={<Link2 className="w-3.5 h-3.5" />}
        label="Share Link"
        flashed={linkFlash}
      />
    </div>
  );
}

function ExportButton({ onClick, icon, label, flashed }) {
  return (
    <button
      onClick={onClick}
      className={[
        "inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border text-xs font-semibold",
        "transition-all duration-200 font-sans",
        flashed
          ? "bg-emerald-50 border-emerald-200 text-emerald-700"
          : "bg-white border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-600 hover:bg-brand-50",
      ].join(" ")}
    >
      {flashed ? <Check className="w-3.5 h-3.5" /> : icon}
      {flashed ? "Copied!" : label}
    </button>
  );
}
