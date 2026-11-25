import React from "react";
import { TOOL_FAMILIES } from "../../resultViews.js";
import { GovernedToolWorkspace } from "./GovernedToolWorkspace.jsx";

// The dedicated surfaces TGT-003 names for GIS, OCR / page review and code. Each one inspects and repeats governed
// tool calls of its kind; files outside the project need a folder grant (Sources), exactly as in chat.

const pathField = (label, placeholder) => ({ name: "path", label, placeholder, required: true });

export function GisWorkspace({ announce }) {
  return (
    <GovernedToolWorkspace
      id="gis"
      title="GIS"
      intro="Inspect GeoJSON layers: coordinate system, geometry validity and where features sit. Each inspection waits for your approval."
      tools={TOOL_FAMILIES.gis}
      announce={announce}
      emptyText="No GIS inspections yet. Ask in chat or request one above."
      request={{
        tool: "gis.inspect",
        label: "Inspect layer",
        hint: "Files outside the project need a folder grant in Sources first.",
        fields: [pathField("GeoJSON file", "/path/to/layer.geojson")],
        build: (values) => ({ path: values.path.trim() }),
      }}
    />
  );
}

export function OcrWorkspace({ announce }) {
  return (
    <GovernedToolWorkspace
      id="ocr"
      title="OCR / page review"
      intro="Read the text in a scanned page or image, word by word with confidence, so low-confidence words can be checked."
      tools={TOOL_FAMILIES.ocr}
      announce={announce}
      emptyText="No pages read yet. Ask in chat or request one above."
      request={{
        tool: "ocr.analyze_image",
        label: "Read page",
        hint: "Uses the Tesseract engine on this computer; files outside the project need a folder grant in Sources.",
        fields: [pathField("Image file", "/path/to/page.png")],
        build: (values) => ({ path: values.path.trim() }),
      }}
    />
  );
}

export function CodeWorkspace({ announce }) {
  return (
    <GovernedToolWorkspace
      id="code"
      title="Code"
      intro="Repository status, changes, files and test runs. Only read-only inspections start here; changes to a repository stay in chat, where each one is proposed and approved."
      tools={TOOL_FAMILIES.code}
      announce={announce}
      emptyText="No code actions yet. Ask in chat or check a repository above."
      request={{
        tool: "coding.repo_status",
        label: "Check repository",
        hint: "The repository folder needs a grant in Sources.",
        fields: [{ name: "repo_root", label: "Repository folder", placeholder: "/path/to/repo", required: true }],
        build: (values) => ({ repo_root: values.repo_root.trim() }),
      }}
    />
  );
}
