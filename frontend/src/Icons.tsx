import type { ReactNode, SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 18, children, ...rest }: P & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>{children}</svg>
  );
}

export const IconPlus = (p: P) => <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>;
export const IconClose = (p: P) => <Svg {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>;
export const IconMenu = (p: P) => <Svg {...p}><path d="M4 7h16M4 12h16M4 17h16" /></Svg>;
export const IconSend = (p: P) => <Svg {...p}><path d="M12 19V5M5 12l7-7 7 7" /></Svg>;
export const IconStop = (p: P) => <Svg {...p}><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" /></Svg>;
export const IconCopy = (p: P) => <Svg {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 012-2h9" /></Svg>;
export const IconCheck = (p: P) => <Svg {...p}><path d="M5 12.5l4.500 4.500L19 7.500" /></Svg>;
export const IconTrash = (p: P) => <Svg {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12M9 7V4h6v3" /></Svg>;
export const IconSettings = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="3" /><path d="M19.400 15a1.700 1.700 0 00.340 1.870l.060.060a2 2 0 11-2.830 2.830l-.060-.060a1.700 1.700 0 00-1.870-.340 1.700 1.700 0 00-1 1.550V21a2 2 0 01-4 0v-.090a1.700 1.700 0 00-1.110-1.550 1.700 1.700 0 00-1.870.340l-.060.060a2 2 0 11-2.830-2.830l.060-.060a1.700 1.700 0 00.340-1.870 1.700 1.700 0 00-1.550-1H3a2 2 0 010-4h.090A1.700 1.700 0 004.600 8.910a1.700 1.700 0 00-.340-1.870l-.060-.060A2 2 0 117.030 4.150l.060.060a1.700 1.700 0 001.870.340H9a1.700 1.700 0 001-1.550V3a2 2 0 014 0v.090a1.700 1.700 0 001 1.550 1.700 1.700 0 001.870-.340l.060-.060a2 2 0 112.830 2.830l-.060.060a1.700 1.700 0 00-.340 1.870V9a1.700 1.700 0 001.550 1H21a2 2 0 010 4h-.090a1.700 1.700 0 00-1.550 1z" /></Svg>;
export const IconGlobe = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.500 2.700 3.800 5.700 3.800 9s-1.300 6.300-3.800 9c-2.500-2.700-3.800-5.700-3.800-9S9.500 5.700 12 3z" /></Svg>;
export const IconChat = (p: P) => <Svg {...p}><path d="M21 12a8 8 0 01-11.600 7.100L4 20l1-4.600A8 8 0 1121 12z" /></Svg>;
export const IconCode = (p: P) => <Svg {...p}><path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" /></Svg>;
export const IconGauge = (p: P) => <Svg {...p}><path d="M4 18a8 8 0 1116 0M12 18l4-6" /></Svg>;
export const IconSearch = (p: P) => <Svg {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.500-3.500" /></Svg>;
export const IconSun = (p: P) => <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.900 4.900l1.400 1.400M17.700 17.700l1.400 1.400M2 12h2M20 12h2M4.900 19.100l1.400-1.400M17.700 6.300l1.400-1.400" /></Svg>;
export const IconMoon = (p: P) => <Svg {...p}><path d="M21 13A9 9 0 1111 3a7 7 0 0010 10z" /></Svg>;
export const IconMonitor = (p: P) => <Svg {...p}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></Svg>;
export const IconAlert = (p: P) => <Svg {...p}><path d="M12 3l10 18H2L12 3zM12 10v5M12 18h.010" /></Svg>;
export const IconArrowDown = (p: P) => <Svg {...p}><path d="M12 5v14M5 12l7 7 7-7" /></Svg>;
export const IconFolder = (p: P) => <Svg {...p}><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" /></Svg>;
export const IconChevron = (p: P) => <Svg {...p}><path d="M9 6l6 6-6 6" /></Svg>;
export const IconSidebar = (p: P) => <Svg {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></Svg>;

/** Phoenix mark: a flame over a wing arc, ember gradient. `live` adds the flicker; `busy` speeds it up while generating. */
export function Logo({ size = 28, live = false, busy = false }: { size?: number; live?: boolean; busy?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className={live ? `logo-live${busy ? " logo-busy" : ""}` : undefined}>
      <defs><linearGradient id="phx-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#ff5a1f" /><stop offset="1" stopColor="#ffa31a" /></linearGradient></defs>
      <path fill="url(#phx-g)" d="M16 3c1 4 5 6 5 11 0 2-1 3-2 4 .3-2-.7-3.500-2-4.500.4 2.500-2 3.500-2 6 0 1.500 1 2.500 2 3-3 .5-6-1.500-6-5 0-3 2-4.500 3-7 .8-1.800 1.500-3.500 2-7.500zM8 22c3 4 13 4 16 0-1 5-5 8-8 8s-7-3-8-8z" />
    </svg>
  );
}
