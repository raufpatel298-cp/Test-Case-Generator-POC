"use client";

import {
  ClassicEditor,
  Essentials,
  Paragraph,
  Bold,
  Italic,
  List,
  Code,
  Table,
  TableToolbar,
  Undo,
} from "ckeditor5";
import { CKEditor } from "@ckeditor/ckeditor5-react";
import "ckeditor5/ckeditor5.css";

function normalizeRichHtml(value: string) {
  return value
    .replace(/\r?\n/g, "")
    .replace(/>\s+</g, "><")
    .trim();
}

export default function RichTextEditor({
  value,
  onChange,
  placeholder,
  minHeight = 120,
  editorClassName = "",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  minHeight?: number;
  editorClassName?: string;
}) {
  return (
    <div
      className={`ckeditor-field ${editorClassName}`}
      style={
        {
          "--ckeditor-min-height": `${minHeight}px`,
        } as React.CSSProperties
      }
    >
      <CKEditor
        editor={ClassicEditor}
        data={value || ""}
        config={{
          licenseKey: "GPL",
          placeholder,
          plugins: [
            Essentials,
            Paragraph,
            Bold,
            Italic,
            Code,
            List,
            Table,
            TableToolbar,
            Undo,
          ],
          toolbar: {
            items: [
              "undo",
              "redo",
              "|",
              "bold",
              "italic",
              "code",
              "|",
              "bulletedList",
              "numberedList",
              "|",
              "paragraph",
              "|",
              "insertTable",
            ],
            shouldNotGroupWhenFull: true,
          },
          table: {
            contentToolbar: [
              "tableColumn",
              "tableRow",
              "mergeTableCells",
            ],
          },
        }}
        onChange={(_, editor) => {
          onChange(normalizeRichHtml(editor.getData()));
        }}
      />
    </div>
  );
}
