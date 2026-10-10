import type { ReactNode, SVGProps } from "react";

/** Набор линейных пиктограмм (24×24, штрих 1.8, цвет — currentColor). Без внешних библиотек: ничего не грузится из сети. */
const PATHS: Record<string, ReactNode> = {
  mic: <><path d="M12 14.5a3.2 3.2 0 0 0 3.2-3.2V6.2a3.2 3.2 0 0 0-6.4 0v5.1a3.2 3.2 0 0 0 3.2 3.2Z" /><path d="M18.5 11.2a6.5 6.5 0 0 1-13 0M12 17.8V21M9 21h6" /></>,
  micOff: <><path d="M9 9.2V6.2a3.2 3.2 0 0 1 6.1-1.3M15.2 10.4v.9a3.2 3.2 0 0 1-4.8 2.8" /><path d="M18.5 11.2c0 .9-.2 1.7-.6 2.5M5.5 11.2a6.5 6.5 0 0 0 9.4 5.8M12 17.8V21M9 21h6M3.5 3.5l17 17" /></>,
  video: <><rect x="3" y="6.2" width="12.5" height="11.6" rx="2.4" /><path d="m15.5 10.4 5.2-3v9.2l-5.2-3" /></>,
  videoOff: <><path d="M9.6 6.2h3.5a2.4 2.4 0 0 1 2.4 2.4v3.1M15.5 15.4v.2a2.4 2.4 0 0 1-2.4 2.2H5.4A2.4 2.4 0 0 1 3 15.4V8.6c0-.8.4-1.5 1-1.9" /><path d="m15.5 10.4 5.2-3v9.2l-3.4-2M3.5 3.5l17 17" /></>,
  screen: <><rect x="3" y="4" width="18" height="12.2" rx="2.4" /><path d="M8 20.2h8M12 16.2v4" /><path d="M12 12.6V8m0 0-2.2 2.2M12 8l2.2 2.2" /></>,
  screenStop: <><rect x="3" y="4" width="18" height="12.2" rx="2.4" /><path d="M8 20.2h8M12 16.2v4" /><rect x="9.4" y="7.4" width="5.2" height="5.2" rx="1" fill="currentColor" stroke="none" /></>,
  record: <><circle cx="12" cy="12" r="8.2" /><circle cx="12" cy="12" r="3.6" fill="currentColor" stroke="none" /></>,
  recordStop: <><circle cx="12" cy="12" r="8.2" /><rect x="8.8" y="8.8" width="6.4" height="6.4" rx="1.2" fill="currentColor" stroke="none" /></>,
  noise: <><path d="M4 10.2v3.6M8 7v10M12 3.8v16.4M16 7.8v8.4M20 10.2v3.6" /></>,
  noiseOff: <><path d="M4 10.2v3.6M8 7v10M12 3.8v16.4M16 7.8v8.4M20 10.2v3.6" opacity=".55" /><path d="M3.5 3.5l17 17" /></>,
  hangup: <><path d="M3.2 14.6c4.9-4.6 12.7-4.6 17.6 0 .5.5.5 1.3 0 1.8l-1.5 1.5c-.5.5-1.2.5-1.8.1l-2.1-1.5c-.4-.3-.6-.7-.6-1.2v-1.3c-1.7-.5-3.5-.5-5.2 0v1.3c0 .5-.2.9-.6 1.2l-2.1 1.5c-.6.4-1.3.4-1.8-.1l-1.5-1.5c-.5-.5-.5-1.3-.1-1.8Z" /></>,
  power: <><path d="M12 3.2v8.2" /><path d="M6.6 6.6a7.6 7.6 0 1 0 10.8 0" /></>,
  retry: <><path d="M20 12a8 8 0 1 1-2.6-5.9" /><path d="M20 4.5v4.3h-4.3" /></>,
  chat: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6c-.5.4-1.3.1-1.3-.6V16A2.5 2.5 0 0 1 4 13.5Z" /><path d="M8 8.5h8M8 11.5h5" /></>,
  board: <><rect x="3" y="4" width="18" height="13" rx="2.4" /><path d="M7.5 20.5 9 17M16.5 20.5 15 17" /><rect x="6.2" y="7.2" width="4.2" height="3" rx=".8" /><rect x="13.6" y="10.6" width="4.2" height="3" rx=".8" /><path d="M10.4 8.7h1.8c.8 0 1.4.6 1.4 1.4v1" /></>,
  attach: <><path d="m20 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9" /></>,
  send: <><path d="M21 3 10.4 13.6M21 3l-6.6 18-4-7.4L3 9.6 21 3Z" /></>,
  phone: <><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z" /></>,
  gear: <><circle cx="12" cy="12" r="3.1" /><path d="M19.4 14.5a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></>,
  hand: <><path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11M11 10.5V4a1.5 1.5 0 0 1 3 0v6.5M14 10.5V5.5a1.5 1.5 0 0 1 3 0V13M17 9.5a1.5 1.5 0 0 1 3 0V15a7 7 0 0 1-7 7h-1.2a6 6 0 0 1-4.6-2.1L3.6 15.6a1.6 1.6 0 0 1 2.3-2.2L8 15.5" /></>,
  more: <><circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" /></>,
  userx: <><circle cx="9.5" cy="8" r="3.4" /><path d="M3 20c.4-3.6 3-5.6 6.5-5.6 1.4 0 2.6.3 3.6.9M16.5 14.5l5 5M21.5 14.5l-5 5" /></>,
  close: <><path d="M6 6l12 12M18 6 6 18" /></>,
  chevronL: <><path d="m14.5 6-6 6 6 6" /></>,
  chevronR: <><path d="m9.5 6 6 6-6 6" /></>,
  file: <><path d="M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8Z" /><path d="M14 3v5h5M8.5 13h7M8.5 16.5h5" /></>,
  transcript: <><path d="M4 6h16M4 10.5h16M4 15h9.5M4 19.5h6" /><path d="m16.5 17 1.6 1.6 3-3.4" /></>,
  transcriptOff: <><path d="M4 6h16M4 10.5h16M4 15h9.5M4 19.5h6" opacity=".55" /><path d="M3.5 3.5l17 17" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="10" rx="2.4" /><path d="M8.2 10.5V8a3.8 3.8 0 0 1 7.6 0v2.5" /></>,
  users: <><circle cx="9" cy="8.5" r="3.2" /><path d="M2.8 19.6c.4-3.4 3-5.3 6.2-5.3s5.8 1.9 6.2 5.3" /><path d="M16 5.6a3.1 3.1 0 0 1 0 5.8M18.2 14.6c1.8.6 3 2.2 3.2 5" /></>,
  arrowR: <><path d="M5 12h14M13.5 6.5 19 12l-5.5 5.5" /></>,
  sparkle: <><path d="M12 3.2 13.9 9l5.8 1.9-5.8 1.9L12 18.6l-1.9-5.8L4.3 10.9 10.1 9Z" /><path d="M19 3.5v3M17.5 5h3" /></>,
  shield: <><path d="M12 3.4 19.4 6v5.6c0 4.5-3 7.5-7.4 9-4.4-1.5-7.4-4.5-7.4-9V6Z" /><path d="m8.8 12.2 2.2 2.2 4.2-4.6" /></>,
  search: <><circle cx="10.8" cy="10.8" r="6" /><path d="m15.4 15.4 5 5" /></>,
  eye: <><path d="M2.6 12S6 5.6 12 5.6 21.4 12 21.4 12 18 18.4 12 18.4 2.6 12 2.6 12Z" /><circle cx="12" cy="12" r="2.8" /></>,
  eyeOff: <><path d="M9.4 6.1c.8-.3 1.6-.5 2.6-.5 6 0 9.4 6.4 9.4 6.4s-.9 1.7-2.6 3.3M6.3 7.8C3.9 9.4 2.6 12 2.6 12S6 18.4 12 18.4c1.5 0 2.8-.4 3.9-.9" /><path d="M10 10.2a2.8 2.8 0 0 0 3.8 3.8M3.5 3.5l17 17" /></>,
  copy: <><rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.4" /><path d="M15.5 8.5V6.4A2.4 2.4 0 0 0 13.1 4H6.4A2.4 2.4 0 0 0 4 6.4v6.7a2.4 2.4 0 0 0 2.4 2.4h2.1" /></>,
  grid: <><rect x="4" y="4" width="6.8" height="6.8" rx="1.8" /><rect x="13.2" y="4" width="6.8" height="6.8" rx="1.8" /><rect x="4" y="13.2" width="6.8" height="6.8" rx="1.8" /><rect x="13.2" y="13.2" width="6.8" height="6.8" rx="1.8" /></>,
  list: <><path d="M9 6.5h11M9 12h11M9 17.5h11" /><circle cx="4.8" cy="6.5" r="1.1" fill="currentColor" stroke="none" /><circle cx="4.8" cy="12" r="1.1" fill="currentColor" stroke="none" /><circle cx="4.8" cy="17.5" r="1.1" fill="currentColor" stroke="none" /></>,
  chevronD: <><path d="m6 9.5 6 6 6-6" /></>,
  expandAll: <><path d="m7 5.5 5 5 5-5M7 13.5l5 5 5-5" /></>,
  collapseAll: <><path d="m7 10.5 5-5 5 5M7 18.5l5-5 5 5" /></>,
  sliders: <><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></>,
  user: <><circle cx="12" cy="8.5" r="3.6" /><path d="M5 20c.8-3.6 3.6-5.6 7-5.6s6.2 2 7 5.6" /></>,
  pin: <><path d="M9 4h6l-1 5 3 3v1.5H7V12l3-3-1-5ZM12 13.5V20" /></>,
  unpin: <><path d="M9 4h6l-1 5 3 3v1.5H7V12l3-3-1-5ZM12 13.5V20M4 4l16 16" /></>,
  layout: <><rect x="3.5" y="4" width="17" height="16" rx="2.4" /><path d="M3.5 14.5h17M9.2 14.5V20M14.8 14.5V20" /></>,
  expand: <><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></>,
  spot: <><circle cx="12" cy="12" r="3.2" /><path d="M12 3v2.6M12 18.4V21M3 12h2.6M18.4 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" /></>,
  pip: <><rect x="3" y="5" width="18" height="14" rx="2.4" /><rect x="12" y="11.5" width="6.5" height="5" rx="1.2" /></>,
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 22, ...rest }: { name: IconName; size?: number } & Omit<SVGProps<SVGSVGElement>, "name">) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"
         aria-hidden focusable="false" {...rest}>
      {PATHS[name]}
    </svg>
  );
}
