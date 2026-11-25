// 元の音(A)と処理あり(B)を同時に同じ位置で鳴らし、ゲインだけを切り替える再生器。
// 切り替えはサンプル単位で同期しているので、A/B 比較で位置がずれない。

export class DualPlayer {
  constructor(sampleRate) {
    this.ctx = new AudioContext({ sampleRate, latencyHint: 'interactive' });
    this.master = this.ctx.createGain();
    this.master.connect(this.ctx.destination);
    this.gainA = this.ctx.createGain();
    this.gainB = this.ctx.createGain();
    this.gainA.connect(this.master);
    this.gainB.connect(this.master);
    this.bufA = null;
    this.bufB = null;
    this.mode = 'B';
    this.gainA.gain.value = 0;
    this.gainB.gain.value = 1;
    this.playing = false;
    this.pos = 0;          // 停止中の位置(秒)
    this.loop = null;      // {start, end}
    this._src = [];
    this._startCtx = 0;
    this._offset = 0;
    this.onEnded = null;
  }

  async decode(arrayBuffer) {
    return await this.ctx.decodeAudioData(arrayBuffer);
  }

  get duration() { return this.bufA ? this.bufA.duration : 0; }

  setBuffers(a, b) {
    this.bufA = a;
    this.bufB = b;
  }

  get time() {
    if (!this.playing) return this.pos;
    // 開始は 30ms 先に予約しているので、その前は経過時間を 0 とみなす(負の位置を返さない)
    const elapsed = Math.max(0, this.ctx.currentTime - this._startCtx);
    let t = this._offset + elapsed;
    if (this.loop && t >= this.loop.end) {
      const len = this.loop.end - this.loop.start;
      t = this.loop.start + ((t - this.loop.start) % len);
    }
    return Math.max(0, Math.min(t, this.duration));
  }

  setMode(mode) {
    this.mode = mode;
    const now = this.ctx.currentTime;
    const ramp = 0.008; // クリックを避ける短いランプ
    for (const [g, on] of [[this.gainA, mode === 'A'], [this.gainB, mode === 'B']]) {
      g.gain.cancelScheduledValues(now);
      g.gain.setValueAtTime(g.gain.value, now);
      g.gain.linearRampToValueAtTime(on ? 1 : 0, now + ramp);
    }
  }

  play(offset = null, loop = undefined) {
    if (!this.bufA || !this.bufB) return;
    if (loop !== undefined) this.loop = loop;
    let t = offset ?? this.time;
    if (!Number.isFinite(t)) t = 0;
    t = Math.max(0, Math.min(t, this.duration));
    if (this.loop) {
      if (t < this.loop.start || t >= this.loop.end) t = this.loop.start;
    } else if (t >= this.duration - 0.01) {
      t = 0;
    }
    this._stopSources();
    this.ctx.resume();
    const when = this.ctx.currentTime + 0.03;
    const mk = (buf, gain) => {
      const s = this.ctx.createBufferSource();
      s.buffer = buf;
      if (this.loop) {
        s.loop = true;
        s.loopStart = this.loop.start;
        s.loopEnd = this.loop.end;
      }
      s.connect(gain);
      s.start(when, t);
      return s;
    };
    const a = mk(this.bufA, this.gainA);
    const b = mk(this.bufB, this.gainB);
    this._src = [a, b];
    b.onended = () => {
      if (this._src[1] !== b) return; // 再始動で止めたもの
      this.playing = false;
      this.pos = this.loop ? this.loop.start : 0;
      this.onEnded && this.onEnded();
    };
    this._startCtx = when;
    this._offset = t;
    this.playing = true;
  }

  stop() {
    if (this.playing) this.pos = this.time;
    this._stopSources();
    this.playing = false;
  }

  seek(t) {
    t = Math.max(0, Math.min(t, this.duration));
    if (this.playing) {
      // ループ中に範囲外をクリックしたらループを解除する
      if (this.loop && (t < this.loop.start || t >= this.loop.end)) this.loop = null;
      this.play(t);
    } else {
      this.pos = t;
    }
  }

  // B の中身が変わった後、再生中なら同じ位置から鳴らし直す
  // (再生中の AudioBufferSourceNode は開始時の内容を使い続けるため)
  refresh() {
    if (this.playing) this.play(this.time);
  }

  _stopSources() {
    for (const s of this._src) {
      try { s.onended = null; s.stop(); } catch (e) { /* 未開始 */ }
      s.disconnect();
    }
    this._src = [];
  }
}
