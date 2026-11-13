import React from "react";

// Line icons drawn for Mucaro (24×24 grid, stroked in the current text colour). They are decorative: the button
// that holds one carries the accessible name.
function Icon({ name, children }) {
  return (
    <svg
      className={`icon icon-${name}`}
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const CopyIcon = () => (
  <Icon name="copy">
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
  </Icon>
);

export const CheckIcon = () => (
  <Icon name="check">
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Icon>
);

export const EditIcon = () => (
  <Icon name="edit">
    <path d="M4 20l1-4L16 5l3 3L8 19z" />
    <path d="M14 7l3 3" />
  </Icon>
);

export const SpeakerIcon = () => (
  <Icon name="speaker">
    <path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" />
    <path d="M15.5 9.5a3.5 3.5 0 0 1 0 5" />
    <path d="M18 7a7 7 0 0 1 0 10" />
  </Icon>
);

export const StopIcon = () => (
  <Icon name="stop">
    <rect x="7" y="7" width="10" height="10" rx="1.5" />
  </Icon>
);

export const SaveIcon = () => (
  <Icon name="save">
    <path d="M12 4v11" />
    <path d="M7.5 10.5L12 15l4.5-4.5" />
    <path d="M5 17v2h14v-2" />
  </Icon>
);

export const ShareIcon = () => (
  <Icon name="share">
    <path d="M12 15V4" />
    <path d="M8 8l4-4 4 4" />
    <path d="M7 11H6a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-7a1 1 0 0 0-1-1h-1" />
  </Icon>
);
