const CACHE_DATABASE = 'melopet-audio-analysis';
const CACHE_STORE = 'features';
const CACHE_SCHEMA_VERSION = 1;

export const AUDIO_FEATURE_VERSION = 'melopet-audio-features@1.0.0';

const FINGERPRINT_SLICE_BYTES = 64 * 1024;
const ANALYSIS_SAMPLE_RATE = 8_000;
const ANALYSIS_WINDOW_SECONDS = 12;
const ANALYSIS_MAX_WINDOWS = 3;
const FFT_SIZE = 1_024;
const SPECTRAL_FRAME_COUNT = 24;

export type FastAudioFeatures = {
  energy: number;
  brightness: number;
  dynamics: number;
  pulse: number;
  tempoConfidence: number;
  bpm?: number;
  analyzedSeconds: number;
  durationSeconds: number;
  version: string;
};

export type AudioFeatureResult = {
  features: FastAudioFeatures | null;
  cacheHit: boolean;
};

type CacheRecord = {
  key: string;
  version: string;
  updatedAt: number;
  features: FastAudioFeatures;
};

const memoryCache = new Map<string, FastAudioFeatures>();
let databasePromise: Promise<IDBDatabase | null> | null = null;

function clamp(value: number, minimum = 0, maximum = 1) {
  return Math.min(maximum, Math.max(minimum, value));
}

function round(value: number, digits = 4) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function percentile(sorted: number[], fraction: number) {
  if (sorted.length === 0) return 0;
  const index = clamp(fraction) * (sorted.length - 1);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') {
      resolve(null);
      return;
    }
    const request = indexedDB.open(CACHE_DATABASE, CACHE_SCHEMA_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(CACHE_STORE)) {
        database.createObjectStore(CACHE_STORE, { keyPath: 'key' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
  return databasePromise;
}

async function readCachedFeatures(key: string): Promise<FastAudioFeatures | null> {
  const inMemory = memoryCache.get(key);
  if (inMemory) return inMemory;
  const database = await openDatabase();
  if (!database) return null;
  return new Promise((resolve) => {
    const transaction = database.transaction(CACHE_STORE, 'readonly');
    const request = transaction.objectStore(CACHE_STORE).get(key);
    request.onsuccess = () => {
      const record = request.result as CacheRecord | undefined;
      if (!record || record.version !== AUDIO_FEATURE_VERSION) {
        resolve(null);
        return;
      }
      memoryCache.set(key, record.features);
      resolve(record.features);
    };
    request.onerror = () => resolve(null);
  });
}

async function writeCachedFeatures(key: string, features: FastAudioFeatures) {
  memoryCache.set(key, features);
  const database = await openDatabase();
  if (!database) return;
  await new Promise<void>((resolve) => {
    const transaction = database.transaction(CACHE_STORE, 'readwrite');
    transaction.objectStore(CACHE_STORE).put({
      key,
      version: AUDIO_FEATURE_VERSION,
      updatedAt: Date.now(),
      features,
    } satisfies CacheRecord);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
    transaction.onabort = () => resolve();
  });
}

async function fileFingerprint(file: File) {
  const tailStart = Math.max(0, file.size - FINGERPRINT_SLICE_BYTES);
  const middleStart = Math.max(0, Math.floor(file.size / 2) - Math.floor(FINGERPRINT_SLICE_BYTES / 2));
  const [head, middle, tail] = await Promise.all([
    file.slice(0, FINGERPRINT_SLICE_BYTES).arrayBuffer(),
    file.slice(middleStart, middleStart + FINGERPRINT_SLICE_BYTES).arrayBuffer(),
    file.slice(tailStart).arrayBuffer(),
  ]);
  const metadata = new TextEncoder().encode(`${file.size}|${file.type}|`);
  const payload = new Uint8Array(metadata.length + head.byteLength + middle.byteLength + tail.byteLength);
  payload.set(metadata, 0);
  payload.set(new Uint8Array(head), metadata.length);
  payload.set(new Uint8Array(middle), metadata.length + head.byteLength);
  payload.set(new Uint8Array(tail), metadata.length + head.byteLength + middle.byteLength);
  if (!globalThis.crypto?.subtle) {
    return `${AUDIO_FEATURE_VERSION}:${file.size}:${file.lastModified}:${file.name}`;
  }
  const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', payload));
  const hex = Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${AUDIO_FEATURE_VERSION}:${hex}`;
}

function analysisSegments(duration: number) {
  if (duration <= ANALYSIS_WINDOW_SECONDS * ANALYSIS_MAX_WINDOWS) {
    return [{ start: 0, duration }];
  }
  const centers = [0.16, 0.5, 0.84];
  return centers.map((fraction) => ({
    start: clamp(
      duration * fraction - ANALYSIS_WINDOW_SECONDS / 2,
      0,
      duration - ANALYSIS_WINDOW_SECONDS,
    ),
    duration: ANALYSIS_WINDOW_SECONDS,
  }));
}

function sampleAudioBuffer(buffer: AudioBuffer) {
  const segments = analysisSegments(buffer.duration);
  const totalSeconds = segments.reduce((sum, segment) => sum + segment.duration, 0);
  const output = new Float32Array(Math.max(1, Math.floor(totalSeconds * ANALYSIS_SAMPLE_RATE)));
  const channelCount = Math.min(2, buffer.numberOfChannels);
  const channels = Array.from({ length: channelCount }, (_, index) => buffer.getChannelData(index));
  let outputIndex = 0;

  for (const segment of segments) {
    const outputLength = Math.floor(segment.duration * ANALYSIS_SAMPLE_RATE);
    const sourceStart = Math.floor(segment.start * buffer.sampleRate);
    const sourceStep = buffer.sampleRate / ANALYSIS_SAMPLE_RATE;
    for (let index = 0; index < outputLength && outputIndex < output.length; index += 1) {
      const sourceIndex = Math.min(
        buffer.length - 1,
        sourceStart + Math.floor(index * sourceStep),
      );
      let value = 0;
      for (const channel of channels) value += channel[sourceIndex] ?? 0;
      output[outputIndex] = value / Math.max(1, channelCount);
      outputIndex += 1;
    }
  }
  return outputIndex === output.length ? output : output.slice(0, outputIndex);
}

function reverseBits(value: number, bits: number) {
  let reversed = 0;
  for (let index = 0; index < bits; index += 1) {
    reversed = (reversed << 1) | (value & 1);
    value >>>= 1;
  }
  return reversed;
}

function fftInPlace(real: Float64Array, imaginary: Float64Array) {
  const size = real.length;
  const bits = Math.log2(size);
  for (let index = 0; index < size; index += 1) {
    const reversed = reverseBits(index, bits);
    if (reversed <= index) continue;
    [real[index], real[reversed]] = [real[reversed], real[index]];
    [imaginary[index], imaginary[reversed]] = [imaginary[reversed], imaginary[index]];
  }
  for (let length = 2; length <= size; length *= 2) {
    const angle = -2 * Math.PI / length;
    const phaseReal = Math.cos(angle);
    const phaseImaginary = Math.sin(angle);
    for (let start = 0; start < size; start += length) {
      let rotationReal = 1;
      let rotationImaginary = 0;
      for (let offset = 0; offset < length / 2; offset += 1) {
        const even = start + offset;
        const odd = even + length / 2;
        const oddReal = real[odd] * rotationReal - imaginary[odd] * rotationImaginary;
        const oddImaginary = real[odd] * rotationImaginary + imaginary[odd] * rotationReal;
        real[odd] = real[even] - oddReal;
        imaginary[odd] = imaginary[even] - oddImaginary;
        real[even] += oddReal;
        imaginary[even] += oddImaginary;
        const nextRotationReal = rotationReal * phaseReal - rotationImaginary * phaseImaginary;
        rotationImaginary = rotationReal * phaseImaginary + rotationImaginary * phaseReal;
        rotationReal = nextRotationReal;
      }
    }
  }
}

function spectralBrightness(samples: Float32Array) {
  if (samples.length < FFT_SIZE) return 0;
  const real = new Float64Array(FFT_SIZE);
  const imaginary = new Float64Array(FFT_SIZE);
  const half = FFT_SIZE / 2;
  let centroidTotal = 0;
  let validFrames = 0;
  const maximumStart = samples.length - FFT_SIZE;

  for (let frame = 0; frame < SPECTRAL_FRAME_COUNT; frame += 1) {
    const start = Math.floor(maximumStart * frame / Math.max(1, SPECTRAL_FRAME_COUNT - 1));
    real.fill(0);
    imaginary.fill(0);
    for (let index = 0; index < FFT_SIZE; index += 1) {
      const window = 0.5 - 0.5 * Math.cos(2 * Math.PI * index / (FFT_SIZE - 1));
      real[index] = samples[start + index] * window;
    }
    fftInPlace(real, imaginary);
    let weighted = 0;
    let magnitudeTotal = 0;
    for (let bin = 1; bin < half; bin += 1) {
      const magnitude = Math.hypot(real[bin], imaginary[bin]);
      weighted += magnitude * bin;
      magnitudeTotal += magnitude;
    }
    if (magnitudeTotal <= 1e-9) continue;
    centroidTotal += weighted / magnitudeTotal / half;
    validFrames += 1;
  }
  return validFrames > 0 ? clamp(centroidTotal / validFrames) : 0;
}

function tempoFeatures(envelope: number[], frameRate: number) {
  if (envelope.length < frameRate * 4) return { bpm: undefined, confidence: 0 };
  const novelty = new Float64Array(envelope.length);
  let noveltyMean = 0;
  for (let index = 4; index < envelope.length; index += 1) {
    const localMean = (
      envelope[index - 1]
      + envelope[index - 2]
      + envelope[index - 3]
      + envelope[index - 4]
    ) / 4;
    novelty[index] = Math.max(0, envelope[index] - localMean);
    noveltyMean += novelty[index];
  }
  noveltyMean /= novelty.length;
  let variance = 0;
  for (let index = 0; index < novelty.length; index += 1) {
    novelty[index] -= noveltyMean;
    variance += novelty[index] ** 2;
  }
  if (variance <= 1e-9) return { bpm: undefined, confidence: 0 };

  const minimumLag = Math.max(1, Math.floor(frameRate * 60 / 180));
  const maximumLag = Math.min(novelty.length - 1, Math.ceil(frameRate * 60 / 60));
  let bestScore = 0;
  let bestLag = 0;
  for (let lag = minimumLag; lag <= maximumLag; lag += 1) {
    let correlation = 0;
    let leftEnergy = 0;
    let rightEnergy = 0;
    for (let index = lag; index < novelty.length; index += 1) {
      const left = novelty[index];
      const right = novelty[index - lag];
      correlation += left * right;
      leftEnergy += left * left;
      rightEnergy += right * right;
    }
    const normalized = correlation / Math.sqrt(Math.max(1e-12, leftEnergy * rightEnergy));
    const bpm = frameRate * 60 / lag;
    const centerBias = 0.94 + 0.06 * (1 - Math.min(1, Math.abs(bpm - 120) / 120));
    const score = normalized * centerBias;
    if (score > bestScore) {
      bestScore = score;
      bestLag = lag;
    }
  }
  if (bestLag === 0) return { bpm: undefined, confidence: 0 };
  return {
    bpm: Math.round(frameRate * 60 / bestLag),
    confidence: clamp(bestScore),
  };
}

export function extractFastAudioFeatures(samples: Float32Array): FastAudioFeatures {
  if (samples.length === 0) {
    return {
      energy: 0,
      brightness: 0,
      dynamics: 0,
      pulse: 0,
      tempoConfidence: 0,
      analyzedSeconds: 0,
      durationSeconds: 0,
      version: AUDIO_FEATURE_VERSION,
    };
  }

  let sumSquares = 0;
  for (const sample of samples) sumSquares += sample * sample;
  const rms = Math.sqrt(sumSquares / samples.length);
  const rmsDb = 20 * Math.log10(Math.max(1e-9, rms));
  const energy = clamp((rmsDb + 36) / 28);

  const frameSize = 256;
  const hopSize = 128;
  const frameRate = ANALYSIS_SAMPLE_RATE / hopSize;
  const envelope: number[] = [];
  const levelsDb: number[] = [];
  for (let start = 0; start + frameSize <= samples.length; start += hopSize) {
    let frameSquares = 0;
    for (let index = 0; index < frameSize; index += 2) {
      const sample = samples[start + index];
      frameSquares += sample * sample;
    }
    const frameRms = Math.sqrt(frameSquares / (frameSize / 2));
    envelope.push(frameRms);
    levelsDb.push(20 * Math.log10(Math.max(1e-9, frameRms)));
  }
  levelsDb.sort((left, right) => left - right);
  const dynamicRangeDb = percentile(levelsDb, 0.9) - percentile(levelsDb, 0.1);
  const dynamics = clamp(dynamicRangeDb / 24);
  const tempo = tempoFeatures(envelope, frameRate);

  return {
    energy: round(energy),
    brightness: round(spectralBrightness(samples)),
    dynamics: round(dynamics),
    pulse: round(tempo.confidence),
    tempoConfidence: round(tempo.confidence),
    bpm: tempo.confidence >= 0.2 ? tempo.bpm : undefined,
    analyzedSeconds: round(samples.length / ANALYSIS_SAMPLE_RATE, 2),
    durationSeconds: round(samples.length / ANALYSIS_SAMPLE_RATE, 2),
    version: AUDIO_FEATURE_VERSION,
  };
}

export async function analyzeAudioFile(
  file: File,
  audioContext: AudioContext,
): Promise<AudioFeatureResult> {
  try {
    const key = await fileFingerprint(file);
    const cached = await readCachedFeatures(key);
    if (cached) return { features: cached, cacheHit: true };
    const encoded = await file.arrayBuffer();
    const decoded = await audioContext.decodeAudioData(encoded);
    const features = {
      ...extractFastAudioFeatures(sampleAudioBuffer(decoded)),
      durationSeconds: round(decoded.duration, 2),
    };
    await writeCachedFeatures(key, features);
    return { features, cacheHit: false };
  } catch {
    return { features: null, cacheHit: false };
  }
}

export function recommendedAudioConcurrency() {
  const logicalCores = typeof navigator === 'undefined' ? 4 : navigator.hardwareConcurrency || 4;
  return Math.max(2, Math.min(4, Math.floor(logicalCores / 2)));
}

export async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  task: (item: T, index: number) => Promise<R>,
  onComplete?: (result: R, index: number) => void,
) {
  const results = Array.from<R>({ length: items.length });
  let nextIndex = 0;
  const workers = Array.from(
    { length: Math.min(Math.max(1, concurrency), Math.max(1, items.length)) },
    async () => {
      while (nextIndex < items.length) {
        const index = nextIndex;
        nextIndex += 1;
        const result = await task(items[index], index);
        results[index] = result;
        onComplete?.(result, index);
      }
    },
  );
  await Promise.all(workers);
  return results;
}
