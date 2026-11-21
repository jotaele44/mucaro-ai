import { useEffect, useState } from "react";

// Whether this device reports a network connection (TGT-019). Mucaro's own service runs on this machine and keeps
// working offline; this only decides what needs the network, such as remote models and web tools.
export function isDeviceOnline() {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

export function useOnline() {
  const [online, setOnline] = useState(isDeviceOnline);
  useEffect(() => {
    const update = () => setOnline(isDeviceOnline());
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  return online;
}
