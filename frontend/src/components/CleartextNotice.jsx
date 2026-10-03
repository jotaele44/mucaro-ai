import React from "react";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** True when this page was loaded over plain http from another device's address, not from the computer itself. */
export function isCleartextRemote(location = window.location) {
  return location.protocol === "http:" && !LOOPBACK_HOSTS.has(location.hostname) && !location.hostname.endsWith(".localhost");
}

// Phone access over http is allowed, but the person should know what it exposes while they type a token.
export function CleartextNotice() {
  if (!isCleartextRemote()) return null;
  return (
    <p className="notice" role="note">
      This connection is not encrypted. Anyone on the same network could read what you send, including the access token. Use it only on a network you trust,
      or over a VPN or an https link (see docs/PHONE_ACCESS.md).
    </p>
  );
}
