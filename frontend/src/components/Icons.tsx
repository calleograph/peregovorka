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
  sliders: <><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></>,
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
