/**
 * Шумовой затвор для «усиленного» шумоподавления: ставится ПОСЛЕ RNNoise (нейросеть убирает шум на фоне речи, затвор — остатки в паузах между фразами).
 * Не «рубит» речь: порог привязан к уровню самой речи (медленный «опорный» уровень), у затвора упреждение 24 мс и «антидребезг» 18 мс: короткие щелчки (мышь, клавиша) не открывают затвор, а начало слова не срезается, выдержка 220 мс и плавное закрытие
 * (хвосты слов и короткие паузы внутри фразы остаются). В паузах звук не обнуляется, а ослабляется на `reduceDb` — без «провалов в тишину» и пульсаций.
 * Усиление `makeupDb` по умолчанию 0: цепочка RNNoise → затвор речь не ослабляет (замер tests/live/noise_probe.py). Адаптивную регулировку громкости пробовали и отвергли: пока речи нет,
 * она принимает за речь любой шум и усиливает его; общий уровень остаётся за браузерным автоусилением микрофона. Мягкий ограничитель не даёт клиппинга.
 * Файл — обычный JS без сборки (политика CSP не разрешает data:/inline-код для worklet); тот же код проверяется юнит-тестом и живым замером (tests/live/noise_probe.py).
 */
export const DEFAULTS = { depthDb: 24, floorDb: -64, reduceDb: 14, holdMs: 200, lookaheadMs: 24, debounceMs: 18, attackMs: 2.5, releaseMs: 90, makeupDb: 0 };

export class GateCore {
  constructor(sampleRate, opts = {}) {
    const o = { ...DEFAULTS, ...opts };
    this.o = o;
    const k = (ms) => 1 - Math.exp(-1 / (sampleRate * ms / 1000));
    this.envAtk = k(1.5); this.envRel = k(25); this.fAtk = k(0.3); this.fRel = k(3);           // быстрая огибающая — для «антидребезга»
    this.refAtk = k(400); this.refDecay = Math.pow(10, -6 / 20 / (sampleRate * 12));      // опорный уровень речи падает на 6 дБ за 12 с тишины
    this.gAtk = k(o.attackMs); this.gRel = k(o.releaseMs);
    this.hold = Math.round(sampleRate * o.holdMs / 1000); this.debounce = Math.round(sampleRate * o.debounceMs / 1000); this.above = 0;
    this.D = Math.max(1, Math.round(sampleRate * o.lookaheadMs / 1000));
    this.buf = new Float32Array(this.D + 1); this.w = 0;
    this.env = 0; this.envF = 0; this.ref = 0; this.g = 1; this.holdLeft = 0;
    this.minG = Math.pow(10, -o.reduceDb / 20); this.floor = Math.pow(10, o.floorDb / 20); this.depth = Math.pow(10, -o.depthDb / 20);
    this.makeup = Math.pow(10, o.makeupDb / 20);
  }
  /** Обрабатывает один отсчёт. */
  step(x) {
    const a = Math.abs(x);
    this.env += (a - this.env) * (a > this.env ? this.envAtk : this.envRel);
    this.envF += (a - this.envF) * (a > this.envF ? this.fAtk : this.fRel);
    if (this.env > this.ref) this.ref += (this.env - this.ref) * this.refAtk; else this.ref *= this.refDecay;
    const thr = Math.max(this.floor, this.ref * this.depth);
    if (this.envF > thr) { if (this.above < 2 * this.debounce) this.above++; } else if (this.above > 0) this.above--;                // накопление «слитного» звука; щелчок (мышь, клавиша) до порога не дорастает
    if (this.above >= this.debounce && this.env > thr) this.holdLeft = this.hold; else if (this.holdLeft > 0) this.holdLeft--;
    const open = this.holdLeft > 0;
    const target = open ? 1 : this.minG;
    this.g += (target - this.g) * (target > this.g ? this.gAtk : this.gRel);
    this.buf[this.w] = x;
    this.w = (this.w + 1) % this.buf.length;
    const y = this.buf[this.w] * this.g * this.makeup;            // отсчёт, записанный D шагов назад
    return y > 0.9 ? 0.9 + Math.tanh((y - 0.9) / 0.1) * 0.1 : y < -0.9 ? -0.9 + Math.tanh((y + 0.9) / 0.1) * 0.1 : y;
  }
}

if (typeof registerProcessor === "function") {
  registerProcessor("pg-noise-gate", class extends AudioWorkletProcessor {
    constructor(options) { super(); this.core = new GateCore(sampleRate, (options && options.processorOptions) || {}); }
    process(inputs, outputs) {
      const i = inputs[0] && inputs[0][0]; const o = outputs[0] && outputs[0][0];
      if (!o) return true;
      if (!i) { o.fill(0); return true; }
      for (let n = 0; n < o.length; n++) o[n] = this.core.step(i[n]);
      return true;
    }
  });
}
