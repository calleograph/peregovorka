/**
 * Диктовка сообщений чата: звук микрофона (AudioContext уже на 16 кГц — браузер сам пересэмплирует) → PCM int16 моно кусками по 100 мс
 * и уровень громкости (для индикатора «идёт диктовка»). Обычный JS без сборки: политика CSP разрешает worklet только с этого же сайта.
 */
class Pcm16 extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = 1600;
    this.buf = new Int16Array(this.size);
    this.n = 0;
    this.sq = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.sq += s * s;
        this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.n === this.size) {
          const out = this.buf;
          this.port.postMessage({ pcm: out.buffer, level: Math.sqrt(this.sq / this.size) }, [out.buffer]);
          this.buf = new Int16Array(this.size);
          this.n = 0;
          this.sq = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pg-pcm16", Pcm16);
