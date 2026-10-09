import { describe, expect, it } from "vitest";
import appSrc from "./App.tsx?raw";
import meetingPage from "./pages/MeetingPage.tsx?raw";
import roomsPage from "./pages/RoomsPage.tsx?raw";
import historyPage from "./pages/HistoryPage.tsx?raw";
import loginPage from "./pages/LoginPage.tsx?raw";
import boardViewer from "./board/BoardViewer.tsx?raw";
import mapTab from "./components/MapTab.tsx?raw";
import sendDialog from "./components/SendMaterialsDialog.tsx?raw";
import deliveryLog from "./components/DeliveryLog.tsx?raw";
import productInfo from "./components/ProductInfo.tsx?raw";

// Страницы входа, списка комнат и истории открываются чаще всего: тяжёлый livekit-client (≈520 кБ) не должен попадать в их основной бандл.
// Раньше это случилось незаметно: BoardViewer импортировал diagnostics.ts, а тот — livekit-client (основной бандл вырос с 413 до 953 кБ).
const HEAVY = /from\s+["'](?:\.{1,2}\/)+(?:diagnostics|roomOptions|screenShare|micPrefs)["']|from\s+["']livekit-client["']/;

describe("основной бандл не тянет livekit-client", () => {
  const modules: Record<string, string> = { App: appSrc, MeetingPage: meetingPage, RoomsPage: roomsPage, HistoryPage: historyPage, LoginPage: loginPage,
    BoardViewer: boardViewer, MapTab: mapTab, SendMaterialsDialog: sendDialog, DeliveryLog: deliveryLog, ProductInfo: productInfo };
  for (const [name, src] of Object.entries(modules)) {
    it(name, () => {
      const bad = src.split("\n").filter((l) => HEAVY.test(l) && !/^\s*import\s+type\b/.test(l));
      expect(bad, `${name} статически импортирует тяжёлый модуль`).toEqual([]);
    });
  }
});
