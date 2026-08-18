/**
 * 到达提示音：温和的 5-6-8 上行三音（柔和钟音）。
 *
 * 使用 Web Audio 现场合成而不是打包音频资产——与交互预览稿里用户试听并
 * 选定的声音完全一致，可复现、无第三方素材授权问题。浏览器要求用户手势
 * 后才解锁音频；本应用必须先登录（点击），因此到达时刻的提示音可以直接
 * 播放。音频不可用时安静降级，绝不影响业务。
 */

let audioContext = null;

function ensureContext() {
  if (typeof window === "undefined") return null;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return null;
  audioContext = audioContext || new AudioContextClass();
  return audioContext;
}

function bellNote(context, frequency, at, duration, gain) {
  const oscillator = context.createOscillator();
  const envelope = context.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = frequency;
  oscillator.connect(envelope);
  envelope.connect(context.destination);
  envelope.gain.setValueAtTime(0.0001, at);
  envelope.gain.linearRampToValueAtTime(gain, at + 0.012);
  envelope.gain.exponentialRampToValueAtTime(0.0001, at + duration);
  oscillator.start(at);
  oscillator.stop(at + duration + 0.05);
}

export function playArrivalChime() {
  try {
    const context = ensureContext();
    if (!context) return;
    if (context.state === "suspended") context.resume();
    const start = context.currentTime + 0.02;
    [523.25, 659.25, 783.99].forEach((frequency, index) => {
      const at = start + index * 0.16;
      bellNote(context, frequency, at, 1.0, 0.11);
      bellNote(context, frequency * 2.756, at, 0.45, 0.028);
    });
  } catch {
    /* 音频不可用时安静降级 */
  }
}
