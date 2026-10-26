'use client';

import { useEffect, useEffectEvent, useRef, useState } from 'react';
import Image from 'next/image';
import {
  AlertCircle,
  Check,
  CircleHelp,
  Download,
  FileMusic,
  HeartPulse,
  Home,
  Info,
  Monitor,
  Music2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  Wifi,
  WifiOff,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Progress, ProgressLabel } from '@/components/ui/progress';
import { Spinner } from '@/components/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  analyzeAudioFile,
  mapWithConcurrency,
  recommendedAudioConcurrency,
  type FastAudioFeatures,
} from '@/lib/audio-analysis';
import { ApiError, api, apiBlob, checkHealth, checkReadiness, clearStoredToken, getStoredToken } from '@/lib/melopet-api';
import type {
  AppTab,
  ConsentRecord,
  DeletionRecord,
  ImportRecord,
  Correction,
  MoodCatalogItem,
  MoodProfile,
  ProfileTrack,
  PersistedFlow,
  PetActionsResponse,
  PetAssetCatalog,
  PetCandidatesResponse,
  PetCustomization,
  PlaylistVersion,
  TaskRecord,
} from '@/lib/melopet-types';

const FLOW_KEY = 'melopet.workflow.v1';
const TERMINAL_TASKS = new Set([
  'PROFILE_READY',
  'CANDIDATES_READY',
  'DELETION_COMPLETED',
  'SAFE_FALLBACK_READY',
  'FINAL_FAILED',
  'CANCELLED',
  'RETRYABLE_FAILED',
]);
const PROFILE_PAGE_SIZE = 200; // 逐曲纠正需要全量曲目，一次拉取（后端单页上限 200）
const MAX_AUDIO_FILES = 1000;
const AUDIO_ACCEPT = '.mp3,.m4a,.aac,.flac,.wav,.ogg,.oga,.opus,.wma,.aif,.aiff,audio/*';
const AUDIO_EXTENSIONS = new Set(['mp3', 'm4a', 'aac', 'flac', 'wav', 'ogg', 'oga', 'opus', 'wma', 'aif', 'aiff']);
const AUDIO_MAPPING = {
  title: 'title',
  artist: 'artist',
  album: 'album',
  durationMs: 'durationMs',
  genre: 'genre',
  mood: 'mood',
  bpm: 'bpm',
  tags: 'tags',
  audioEnergy: 'audioEnergy',
  audioBrightness: 'audioBrightness',
  audioDynamics: 'audioDynamics',
  audioPulse: 'audioPulse',
  tempoConfidence: 'tempoConfidence',
  audioFeatureVersion: 'audioFeatureVersion',
} as const;

const TASK_STATUS_LABELS: Record<string, string> = {
  QUEUED: '等待后台处理',
  RESOLVING_SIGNALS: '整理歌曲信号',
  CLASSIFYING: '识别歌曲情绪',
  GUARDING: '检查识别结果',
  COMPUTING: '汇总情绪画像',
  MAPPING_PET: '映射桌宠特征',
  GENERATING_COPY: '生成桌宠文案',
  VALIDATING_ASSETS: '校验素材',
  RENDERING: '渲染桌宠预览',
  PROFILE_READY: '画像已完成',
  CANDIDATES_READY: '桌宠候选已完成',
  RETRYABLE_FAILED: '任务可重试',
  FINAL_FAILED: '任务未完成',
  CANCELLED: '任务已取消',
  DELETING_DATA: '正在删除数据',
  DELETION_COMPLETED: '数据已删除',
};

const QUALITY_LABELS: Record<string, string> = {
  LOW_SAMPLE: '样本较少，画像仅供试玩',
  HIGH_UNKNOWN_RATE: '待确认歌曲较多，建议逐曲纠正',
  NO_KNOWN_TRACKS: '没有可识别歌曲',
};

type ServiceState = 'checking' | 'online' | 'offline';
type PetActionLoadState = 'idle' | 'loading' | 'ready' | 'fallback';
type DesktopPetAction = 'idle' | 'listen' | 'dance' | 'sleep';
type ConfirmAction = 'revoke' | 'delete' | null;
type UiIssue = { code: string; message: string; requestId?: string };
type ParsedSong = {
  id: string;
  dedupeKey: string;
  fileName: string;
  size: number;
  title: string;
  artist: string;
  album: string;
  durationMs?: number;
  genre: string;
  mood: string;
  bpm?: number;
  tags: string;
  audioFeatures?: FastAudioFeatures;
  audioAnalysisSource: 'computed' | 'cache' | 'skipped' | 'unavailable';
  usedEmbeddedTags: boolean;
  warning?: string;
};

function formatDate(value?: string | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function sleep(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function cleanTag(value?: string | null) {
  if (!value) return '';
  const printable = Array.from(value, (character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127 ? ' ' : character;
  }).join('');
  return printable.replace(/\s+/g, ' ').trim();
}

function metadataFromFilename(fileName: string) {
  const baseName = fileName.replace(/\.[^.]+$/, '').trim() || '未命名歌曲';
  const parts = baseName.split(/\s+[-–—]\s+/);
  if (parts.length >= 2) {
    return { artist: parts[0].trim(), title: parts.slice(1).join(' - ').trim() };
  }
  return { artist: '未知艺人', title: baseName };
}

function formatDuration(durationMs?: number) {
  if (!durationMs || durationMs < 1000) return '时长未知';
  const totalSeconds = Math.round(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function csvCell(value: string | number | undefined) {
  return `"${String(value ?? '').replaceAll('"', '""')}"`;
}

function songsToCsv(songs: ParsedSong[]) {
  const headers = Object.values(AUDIO_MAPPING);
  const rows = songs.map((song) => [
    song.title,
    song.artist,
    song.album,
    song.durationMs,
    song.genre,
    song.mood,
    song.bpm,
    song.tags,
    song.audioFeatures?.energy,
    song.audioFeatures?.brightness,
    song.audioFeatures?.dynamics,
    song.audioFeatures?.pulse,
    song.audioFeatures?.tempoConfidence,
    song.audioFeatures?.version,
  ]);
  return [headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\n');
}

function isSupportedAudio(file: File) {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  return file.type.startsWith('audio/') || AUDIO_EXTENSIONS.has(extension);
}

async function readTextFileSmart(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch {
    return new TextDecoder('gb18030').decode(buffer);
  }
}

function errorFrom(error: unknown): UiIssue {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message, requestId: error.requestId };
  }
  return { code: 'UNEXPECTED_ERROR', message: '出现了未预期的问题，请重试。' };
}

function looksLikePlaylistUrl(text: string) {
  const value = text.trim();
  return /music\.163\.com/.test(value) && /(playlist|id=)/.test(value);
}

async function svgBlobToPngDownload(svgBlob: Blob, filename: string) {
  const svgText = await svgBlob.text();
  const url = URL.createObjectURL(new Blob([svgText], { type: 'image/svg+xml' }));
  const image = new window.Image();
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error('SVG 渲染失败'));
    image.src = url;
  });
  const scale = 2;
  const canvas = document.createElement('canvas');
  canvas.width = 1440;
  canvas.height = 2000;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('浏览器不支持画布导出');
  context.scale(scale, scale);
  context.drawImage(image, 0, 0, 720, 1000);
  URL.revokeObjectURL(url);
  const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!png) throw new Error('PNG 编码失败');
  const downloadUrl = URL.createObjectURL(png);
  const anchor = document.createElement('a');
  anchor.href = downloadUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(downloadUrl);
}

function SectionHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div>
      <p className="text-xs font-semibold text-[#8B7CFF]">{eyebrow}</p>
      <h2 className="mt-1 text-[23px] font-bold tracking-tight">{title}</h2>
      <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{description}</p>
    </div>
  );
}

function EmptyState({ icon, title, description, action }: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="grid min-h-[440px] place-items-center text-center">
      <div className="max-w-64">
        <span className="mx-auto mb-4 grid size-14 place-items-center rounded-2xl bg-[#1E2138] text-[#8B7CFF]">{icon}</span>
        <h2 className="text-xl font-bold">{title}</h2>
        <p className="mt-2 leading-6 text-muted-foreground">{description}</p>
        {action && <div className="mt-5">{action}</div>}
      </div>
    </div>
  );
}

export function MeloPetApp() {
  const [tab, setTab] = useState<AppTab>('home');
  const [audioSongs, setAudioSongs] = useState<ParsedSong[]>([]);
  const [parseProgress, setParseProgress] = useState<{
    done: number;
    total: number;
    analyzed: number;
    cached: number;
  } | null>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const [playlistText, setPlaylistText] = useState('');
  const playlistFileRef = useRef<HTMLInputElement>(null);
  const [consentChoice, setConsentChoice] = useState(true);
  const [serviceState, setServiceState] = useState<ServiceState>('checking');
  const [readiness, setReadiness] = useState<Awaited<ReturnType<typeof checkReadiness>> | null>(null);
  const [flow, setFlow] = useState<PersistedFlow>({});
  const [hydrated, setHydrated] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [issue, setIssue] = useState<UiIssue | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [importRecord, setImportRecord] = useState<ImportRecord | null>(null);
  const [playlist, setPlaylist] = useState<PlaylistVersion | null>(null);
  const [activeTask, setActiveTask] = useState<TaskRecord | null>(null);
  const [catalog, setCatalog] = useState<MoodCatalogItem[]>([]);
  const [profile, setProfile] = useState<MoodProfile | null>(null);
  const [pendingCorrections, setPendingCorrections] = useState<Record<string, Correction>>({});
  const [petData, setPetData] = useState<PetCandidatesResponse | null>(null);
  const [petCatalog, setPetCatalog] = useState<PetAssetCatalog | null>(null);
  const [petCustomization, setPetCustomization] = useState<PetCustomization>({});
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
  const previewUrlsRef = useRef<string[]>([]);
  const [cardUrl, setCardUrl] = useState<string | null>(null);
  const cardUrlRef = useRef<string>('');
  const [actionUrls, setActionUrls] = useState<Record<string, string>>({});
  const [petActionLoadState, setPetActionLoadState] = useState<PetActionLoadState>('idle');
  const [petActionsFallbackMode, setPetActionsFallbackMode] = useState(false);
  const [petActionsRefresh, setPetActionsRefresh] = useState(0);
  const [desktopPetAction, setDesktopPetAction] = useState<DesktopPetAction | null>(null);
  const actionUrlsRef = useRef<string[]>([]);
  const actionsPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [consentRecord, setConsentRecord] = useState<ConsentRecord | null>(null);
  const [deletion, setDeletion] = useState<DeletionRecord | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const webStateRef = useRef({ tab, flow, hasProfile: false, candidateCount: 0 });

  function patchFlow(update: Partial<PersistedFlow>) {
    setFlow((current) => ({ ...current, ...update }));
  }

  function changeTab(next: AppTab) {
    setTab(next);
    patchFlow({ tab: next });
  }

  function report(error: unknown) {
    setNotice(null);
    setIssue(errorFrom(error));
  }

  async function handleAudioFiles(files: File[]) {
    if (parseProgress || files.length === 0) return;
    setIssue(null);
    setNotice(null);

    const existingKeys = new Set(audioSongs.map((song) => song.dedupeKey));
    const seenKeys = new Set(existingKeys);
    const supported: File[] = [];
    let unsupportedCount = 0;
    let duplicateCount = 0;

    for (const file of files) {
      if (!isSupportedAudio(file)) {
        unsupportedCount += 1;
        continue;
      }
      const key = `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
      if (seenKeys.has(key)) {
        duplicateCount += 1;
        continue;
      }
      seenKeys.add(key);
      supported.push(file);
    }

    const remainingSlots = Math.max(0, MAX_AUDIO_FILES - audioSongs.length);
    const accepted = supported.slice(0, remainingSlots);
    const overLimitCount = supported.length - accepted.length;
    if (accepted.length === 0) {
      setIssue({
        code: overLimitCount > 0 ? 'TOO_MANY_TRACKS' : 'AUDIO_FILES_REQUIRED',
        message: overLimitCount > 0
          ? `一次最多导入 ${MAX_AUDIO_FILES} 首歌曲。`
          : '没有找到可导入的音频文件，请选择 MP3、M4A、FLAC、WAV 或 OGG 等格式。',
      });
      return;
    }

    setParseProgress({ done: 0, total: accepted.length, analyzed: 0, cached: 0 });
    let parser: typeof import('music-metadata').parseBlob | null = null;
    try {
      ({ parseBlob: parser } = await import('music-metadata'));
    } catch {
      parser = null;
    }
    let audioContext: AudioContext | null = null;
    try {
      audioContext = new AudioContext();
    } catch {
      audioContext = null;
    }
    let completed = 0;
    let analyzed = 0;
    let cached = 0;
    const parsedSongs = await mapWithConcurrency(
      accepted,
      recommendedAudioConcurrency(),
      async (file): Promise<ParsedSong> => {
        const fallback = metadataFromFilename(file.name);
        let song: ParsedSong = {
          id: crypto.randomUUID(),
          dedupeKey: `${file.name}\u0000${file.size}\u0000${file.lastModified}`,
          fileName: file.name,
          size: file.size,
          title: fallback.title,
          artist: fallback.artist,
          album: '',
          genre: '',
          mood: '',
          tags: '',
          audioAnalysisSource: 'unavailable',
          usedEmbeddedTags: false,
          warning: '未找到内嵌标签，已使用文件名',
        };

        if (parser) {
          try {
            const metadata = await parser(file, { skipCovers: true });
            const embeddedTitle = cleanTag(metadata.common.title);
            const embeddedArtist = cleanTag(metadata.common.artist)
              || cleanTag(metadata.common.artists?.join('、'));
            const album = cleanTag(metadata.common.album);
            const genre = cleanTag(metadata.common.genre?.join(' / '));
            const mood = cleanTag(metadata.common.mood);
            const tags = cleanTag([
              metadata.common.grouping,
              ...(metadata.common.keywords ?? []),
              ...(metadata.common.category ?? []),
            ].filter(Boolean).join(' / '));
            const duration = metadata.format.duration;
            const bpm = metadata.common.bpm;
            const hasTags = Boolean(embeddedTitle || embeddedArtist || album || genre || mood || tags || bpm);
            song = {
              ...song,
              title: embeddedTitle || fallback.title,
              artist: embeddedArtist || fallback.artist,
              album,
              genre,
              mood,
              tags,
              durationMs: typeof duration === 'number' && Number.isFinite(duration)
                ? Math.round(duration * 1000)
                : undefined,
              bpm: typeof bpm === 'number' && Number.isFinite(bpm) ? bpm : undefined,
              usedEmbeddedTags: hasTags,
              warning: hasTags
                ? (!embeddedTitle || !embeddedArtist ? '部分标签缺失，已用文件名补全' : undefined)
                : '未找到内嵌标签，已使用文件名',
            };
          } catch {
            song.warning = '无法读取音频标签，已使用文件名';
          }
        }

        const hasCompleteClassificationTags = Boolean(song.genre && song.mood && song.bpm);
        if (!audioContext || hasCompleteClassificationTags) {
          song.audioAnalysisSource = hasCompleteClassificationTags ? 'skipped' : 'unavailable';
          return song;
        }
        const audioResult = await analyzeAudioFile(file, audioContext);
        if (!audioResult.features) return song;
        song.audioFeatures = audioResult.features;
        song.audioAnalysisSource = audioResult.cacheHit ? 'cache' : 'computed';
        song.durationMs ??= Math.round(audioResult.features.durationSeconds * 1000);
        if (!song.bpm && audioResult.features.bpm && audioResult.features.tempoConfidence >= 0.3) {
          song.bpm = audioResult.features.bpm;
        }
        if (!song.usedEmbeddedTags) {
          song.warning = '未找到内嵌标签，已用文件名补全；已分析音频特征';
        } else if (!song.genre || !song.mood) {
          song.warning = '类型标签不完整，已用音频特征补充';
        }
        return song;
      },
      (song) => {
        completed += 1;
        if (song.audioAnalysisSource === 'computed') analyzed += 1;
        if (song.audioAnalysisSource === 'cache') cached += 1;
        setParseProgress({ done: completed, total: accepted.length, analyzed, cached });
      },
    );
    if (audioContext) await audioContext.close().catch(() => undefined);

    setAudioSongs((current) => [...current, ...parsedSongs]);
    setParseProgress(null);
    const fallbackCount = parsedSongs.filter((song) => !song.usedEmbeddedTags).length;
    const skipped = unsupportedCount + duplicateCount + overLimitCount;
    setNotice(
      `已读取 ${parsedSongs.length} 首歌曲`
      + (analyzed > 0 ? `，${analyzed} 首完成音频特征分析` : '')
      + (cached > 0 ? `，${cached} 首命中本地缓存` : '')
      + (fallbackCount > 0 ? `，${fallbackCount} 首使用文件名补全` : '')
      + (skipped > 0 ? `，${skipped} 个文件已跳过` : '')
      + '。',
    );
  }

  async function loadCatalog() {
    const result = await api<{ items: MoodCatalogItem[] }>('/v1/catalogs/moods');
    setCatalog(result.items);
    return result.items;
  }

  async function loadPetCatalog() {
    const result = await api<PetAssetCatalog>('/v1/catalogs/pet-assets');
    setPetCatalog(result);
    return result;
  }

  async function loadProfile(revision: string, page = 1) {
    const result = await api<MoodProfile>(
      `/v1/mood-profiles/${encodeURIComponent(revision)}?page=${page}&page_size=${PROFILE_PAGE_SIZE}`,
    );
    setProfile(result);
    if (catalog.length === 0) await loadCatalog();
    return result;
  }

  async function loadConsent(consentId: string) {
    const result = await api<ConsentRecord>(`/v1/consents/${encodeURIComponent(consentId)}`);
    setConsentRecord(result);
    return result;
  }

  async function loadCandidates(taskId: string) {
    const result = await api<PetCandidatesResponse>(`/v1/pet-candidates/${encodeURIComponent(taskId)}`);
    setPetData(result);
    setActiveTask(result.task);

    if (result.candidates.length > 0) {
      const entries = await Promise.all(
        result.candidates.map(async (candidate) => {
          const blob = await apiBlob(candidate.previewUrl);
          return [candidate.candidateRecordId, URL.createObjectURL(blob)] as const;
        }),
      );
      previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      previewUrlsRef.current = entries.map(([, url]) => url);
      setPreviewUrls(Object.fromEntries(entries));
    }
    if (result.selection) {
      const cardBlob = await apiBlob(
        `/v1/pet-candidates/${encodeURIComponent(taskId)}/cards/${encodeURIComponent(result.selection.candidateRecordId)}`,
      );
      if (cardUrlRef.current) URL.revokeObjectURL(cardUrlRef.current);
      const url = URL.createObjectURL(cardBlob);
      cardUrlRef.current = url;
      setCardUrl(url);
    } else {
      if (cardUrlRef.current) URL.revokeObjectURL(cardUrlRef.current);
      cardUrlRef.current = '';
      setCardUrl(null);
    }
    return result;
  }

  async function waitForTask(taskId: string) {
    for (let attempt = 0; attempt < 75; attempt += 1) {
      const task = await api<TaskRecord>(`/v1/tasks/${encodeURIComponent(taskId)}`);
      setActiveTask(task);
      if (TERMINAL_TASKS.has(task.status)) return task;
      await sleep(800);
    }
    throw new ApiError('任务仍在后台运行。你可以稍后点击“刷新任务状态”继续查看。', 'TASK_POLL_TIMEOUT', 408);
  }

  async function completeTask(task: TaskRecord) {
    if (task.status === 'PROFILE_READY') {
      const revision = task.output.profileRevision;
      if (typeof revision !== 'string') throw new ApiError('画像任务缺少结果版本。', 'INVALID_TASK_OUTPUT', 500);
      patchFlow({ profileRevision: revision });
      await loadProfile(revision);
      changeTab('profile');
      setNotice('情绪画像已由 AI 自动生成，确认后即可生成桌宠。');
      return;
    }
    if (task.status === 'CANDIDATES_READY') {
      await loadCandidates(task.taskId);
      changeTab('pet');
      setNotice('3 个桌宠候选已经生成。');
      return;
    }
    if (task.status === 'CANCELLED') {
      setIssue({ code: 'TASK_CANCELLED', message: '任务已取消，已有数据不会丢失。' });
      return;
    }
    if (task.status === 'RETRYABLE_FAILED' || task.status === 'FINAL_FAILED') {
      setIssue({
        code: task.errorCode ?? task.status,
        message: task.safeMessage ?? '后台任务未能完成。',
      });
    }
  }

  async function resumeTask(taskId: string) {
    setBusy('正在恢复后台任务…');
    try {
      const task = await waitForTask(taskId);
      await completeTask(task);
    } finally {
      setBusy(null);
    }
  }

  function clearWorkflowAfterDeletion() {
    previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    previewUrlsRef.current = [];
    setPreviewUrls({});
    if (cardUrlRef.current) URL.revokeObjectURL(cardUrlRef.current);
    cardUrlRef.current = '';
    setCardUrl(null);
    actionUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    actionUrlsRef.current = [];
    setActionUrls({});
    setPetActionLoadState('idle');
    setImportRecord(null);
    setPlaylist(null);
    setProfile(null);
    setPetData(null);
    setPetCustomization({});
    setConsentRecord(null);
    setActiveTask(null);
    setAudioSongs([]);
    setParseProgress(null);
    setFlow({ tab: 'home' });
    setTab('home');
  }

  async function normalizeImportedSongs(record: ImportRecord) {
    const mapping = Object.fromEntries(
      Object.entries(AUDIO_MAPPING).filter(([, header]) => record.headers.includes(header)),
    );
    if (!('title' in mapping) || !('artist' in mapping)) {
      throw new ApiError('这份旧导入数据无法自动整理，请删除后重新选择歌曲。', 'AUDIO_MAPPING_REQUIRED', 400);
    }
    const mapped = await api<PlaylistVersion>(
      `/v1/playlist-imports/${encodeURIComponent(record.importId)}/mapping`,
      {
        method: 'PUT',
        body: JSON.stringify({ mapping }),
        idempotentWrite: true,
      },
    );
    setPlaylist(mapped);
    patchFlow({ playlistVersion: mapped.playlistVersion });
    return mapped;
  }

  const restoreFlow = useEffectEvent(async (saved: PersistedFlow) => {
    if (!getStoredToken()) return;
    setBusy('正在恢复上次进度…');
    try {
      if (saved.deletionId) {
        const restoredDeletion = await api<DeletionRecord>(`/v1/deletions/${encodeURIComponent(saved.deletionId)}`);
        setDeletion(restoredDeletion);
        if (restoredDeletion.status === 'DELETION_COMPLETED') {
          clearWorkflowAfterDeletion();
          setNotice('上次提交的后端数据删除已经完成。');
          return;
        }
        setTab('privacy');
        return;
      }
      await loadCatalog();
      void loadPetCatalog().catch(() => undefined);
      if (saved.importId) {
        const restoredImport = await api<ImportRecord>(`/v1/playlist-imports/${encodeURIComponent(saved.importId)}`);
        setImportRecord(restoredImport);
        if (restoredImport.latestPlaylistVersion) {
          setPlaylist(restoredImport.latestPlaylistVersion);
        } else if (restoredImport.headers.includes('title') && restoredImport.headers.includes('artist')) {
          await normalizeImportedSongs(restoredImport);
        }
      }
      if (saved.consentId) await loadConsent(saved.consentId);
      if (saved.profileRevision) await loadProfile(saved.profileRevision);
      if (saved.petTaskId) await loadCandidates(saved.petTaskId);

      const taskId = saved.petTaskId ?? saved.analysisTaskId;
      if (taskId) {
        const task = await api<TaskRecord>(`/v1/tasks/${encodeURIComponent(taskId)}`);
        setActiveTask(task);
        if (!TERMINAL_TASKS.has(task.status)) {
          setBusy(null);
          await resumeTask(taskId);
        } else if (!saved.profileRevision && task.status === 'PROFILE_READY') {
          await completeTask(task);
        }
      }
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  });

  useEffect(() => {
    let cancelled = false;
    void Promise.all([checkHealth(), checkReadiness()])
      .then(([, ready]) => {
        if (cancelled) return;
        setServiceState('online');
        setReadiness(ready);
      })
      .catch(() => {
        if (!cancelled) setServiceState('offline');
      });

    let saved: PersistedFlow = {};
    try {
      saved = JSON.parse(window.localStorage.getItem(FLOW_KEY) ?? '{}') as PersistedFlow;
    } catch {
      window.localStorage.removeItem(FLOW_KEY);
    }
    window.queueMicrotask(() => {
      if (cancelled) return;
      setFlow(saved);
      setTab(saved.tab ?? (saved.importId ? 'import' : 'home'));
      setHydrated(true);
      void restoreFlow(saved);
    });
    return () => {
      cancelled = true;
      previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      actionUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      if (cardUrlRef.current) URL.revokeObjectURL(cardUrlRef.current);
    };
  }, []);

  useEffect(() => {
    if (hydrated) window.localStorage.setItem(FLOW_KEY, JSON.stringify(flow));
  }, [flow, hydrated]);

  useEffect(() => {
    webStateRef.current = {
      tab,
      flow,
      hasProfile: Boolean(profile),
      candidateCount: petData?.candidates.length ?? 0,
    };
  }, [tab, flow, profile, petData]);

  useEffect(() => {
    const context = document.modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();

    const register = async () => {
      await context.registerTool(
        {
          name: 'read_melopet_workflow',
          title: '读取 Playlist Pet 进度',
          description: '读取当前可见页面和歌单、画像、桌宠候选的完成状态，不修改任何数据。',
          inputSchema: { type: 'object', properties: {}, additionalProperties: false },
          annotations: { readOnlyHint: true, untrustedContentHint: false },
          execute() {
            const state = webStateRef.current;
            return {
              currentView: state.tab,
              hasImport: Boolean(state.flow.importId),
              hasProfile: state.hasProfile,
              candidateCount: state.candidateCount,
            };
          },
        },
        { signal: lifecycle.signal },
      );
      await context.registerTool(
        {
          name: 'start_melopet_song_import',
          title: '开始导入歌曲',
          description: '打开多首音频文件导入页。文件选择必须由用户在页面中完成，不会自动上传。',
          inputSchema: { type: 'object', properties: {}, additionalProperties: false },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          execute() {
            setTab('import');
            setFlow((current) => ({ ...current, tab: 'import' }));
            return { opened: true, requiresUserFileSelection: true, supportsMultiple: true };
          },
        },
        { signal: lifecycle.signal },
      );
    };

    void register().catch(() => undefined);
    return () => lifecycle.abort();
  }, []);

  async function submitImportText(text: string, busyLabel: string, createNewCopy = false) {
    setBusy(busyLabel);
    setIssue(null);
    setNotice(null);
    try {
      const created = await api<ImportRecord>('/v1/playlist-imports', {
        method: 'POST',
        body: JSON.stringify({
          text,
          consentExternalProcessing: consentChoice,
          createNewCopy,
        }),
        idempotentWrite: true,
      });
      setImportRecord(created);
      setPlaylist(null);
      setProfile(null);
      setPetData(null);
      setPetCustomization({});
      setActiveTask(null);
      patchFlow({
        importId: created.importId,
        consentId: created.consentId,
        playlistVersion: undefined,
        analysisTaskId: undefined,
        profileRevision: undefined,
        petTaskId: undefined,
        deletionId: undefined,
      });
      await loadConsent(created.consentId);
      setBusy('正在整理歌曲信息…');
      const mapped = await normalizeImportedSongs(created);
      setNotice(`已规范化 ${mapped.normalizedCount} 首歌曲。请核对预览后生成画像。`);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function handleImport(createNewCopy = false) {
    if (audioSongs.length === 0) {
      setNotice(null);
      setIssue({ code: 'AUDIO_FILES_REQUIRED', message: '请先选择要导入的歌曲。' });
      return;
    }
    if (audioSongs.length > MAX_AUDIO_FILES) {
      setNotice(null);
      setIssue({ code: 'TOO_MANY_TRACKS', message: `一次最多导入 ${MAX_AUDIO_FILES} 首歌曲。` });
      return;
    }
    await submitImportText(songsToCsv(audioSongs), `正在导入 ${audioSongs.length} 首歌曲…`, createNewCopy);
  }

  async function handlePlaylistTextImport() {
    const text = playlistText.trim();
    if (!text) {
      setNotice(null);
      setIssue({ code: 'PLAYLIST_TEXT_REQUIRED', message: '请先粘贴歌单内容。' });
      return;
    }
    await submitImportText(text, looksLikePlaylistUrl(text) ? '正在解析网易云歌单…' : '正在导入歌单…');
  }

  async function handlePlaylistFile(files: File[]) {
    const file = files[0];
    if (!file) return;
    let text: string;
    try {
      text = await readTextFileSmart(file);
    } catch {
      setNotice(null);
      setIssue({ code: 'PLAYLIST_FILE_UNREADABLE', message: '无法读取这个文件，请换一个 CSV 或 TXT 文件。' });
      return;
    }
    if (playlistFileRef.current) playlistFileRef.current.value = '';
    await submitImportText(text, '正在导入歌单文件…');
  }

  async function handleAnalysis() {
    if (!importRecord || !playlist) return;
    setBusy('正在创建画像任务…');
    setIssue(null);
    setNotice(null);
    try {
      const task = await api<TaskRecord>(
        `/v1/playlist-imports/${encodeURIComponent(importRecord.importId)}/analyses`,
        { method: 'POST', idempotentWrite: true },
      );
      setActiveTask(task);
      patchFlow({ analysisTaskId: task.taskId });
      changeTab('profile');
      const completed = await waitForTask(task.taskId);
      await completeTask(completed);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function refreshActiveTask() {
    if (!activeTask) return;
    setIssue(null);
    try {
      await resumeTask(activeTask.taskId);
    } catch (error) {
      report(error);
    }
  }

  async function cancelActiveTask() {
    if (!activeTask) return;
    setBusy('正在请求取消…');
    setIssue(null);
    try {
      const task = await api<TaskRecord>(`/v1/tasks/${encodeURIComponent(activeTask.taskId)}/cancel`, {
        method: 'POST',
        idempotentWrite: true,
      });
      setActiveTask(task);
      const completed = await waitForTask(task.taskId);
      await completeTask(completed);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function retryActiveTask() {
    if (!activeTask) return;
    setBusy('正在重试任务…');
    setIssue(null);
    try {
      const task = await api<TaskRecord>(`/v1/tasks/${encodeURIComponent(activeTask.taskId)}/retry`, {
        method: 'POST',
        idempotentWrite: true,
      });
      setActiveTask(task);
      const completed = await waitForTask(task.taskId);
      await completeTask(completed);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  function clearPendingCorrection(trackRef: string) {
    setPendingCorrections((current) => {
      const next = { ...current };
      delete next[trackRef];
      return next;
    });
  }

  function toggleTrackExclusion(track: ProfileTrack) {
    const pending = pendingCorrections[track.trackRef];
    const currentlyExcluded = pending?.action === 'exclude'
      ? true
      : (pending?.action === 'include' || pending?.action === 'set_mood' || pending?.action === 'restore_auto')
        ? false
        : track.excluded;
    setPendingCorrections((current) => {
      const next = { ...current };
      if (currentlyExcluded) {
        if (pending?.action === 'exclude') delete next[track.trackRef];
        else next[track.trackRef] = { action: 'include' };
      } else {
        next[track.trackRef] = { action: 'exclude' };
      }
      return next;
    });
  }

  async function applyCorrections() {
    const corrections = Object.entries(pendingCorrections).map(([trackRef, correction]) => ({
      trackRef,
      ...correction,
    }));
    if (!profile || corrections.length === 0) return;
    setBusy('正在保存纠正…');
    setIssue(null);
    setNotice(null);
    try {
      const corrected = await api<MoodProfile>(
        `/v1/mood-profiles/${encodeURIComponent(profile.revision)}/corrections`,
        {
          method: 'POST',
          body: JSON.stringify({ expectedRevision: profile.revision, corrections }),
          idempotentWrite: true,
        },
      );
      setPendingCorrections({});
      patchFlow({ profileRevision: corrected.revision });
      await loadProfile(corrected.revision);
      setNotice(`已保存 ${corrections.length} 处纠正，画像版本更新为第 ${corrected.revisionNumber} 版。`);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function confirmProfile() {
    if (!profile) return;
    setBusy('正在确认画像…');
    setIssue(null);
    try {
      const confirmed = await api<MoodProfile>(
        `/v1/mood-profiles/${encodeURIComponent(profile.revision)}/confirm`,
        { method: 'POST', idempotentWrite: true },
      );
      setProfile((current) => current ? { ...current, ...confirmed, tracks: current.tracks } : current);
      void loadPetCatalog().catch(() => undefined);
      setNotice('画像已确认，现在可以生成 3 个桌宠候选。');
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function generatePets() {
    if (!profile) return;
    setBusy('正在生成桌宠候选…');
    setIssue(null);
    setNotice(null);
    try {
      const task = await api<TaskRecord>(
        `/v1/mood-profiles/${encodeURIComponent(profile.revision)}/pet-candidates`,
        { method: 'POST', body: JSON.stringify({ customization: petCustomization }), idempotentWrite: true },
      );
      setActiveTask(task);
      patchFlow({ petTaskId: task.taskId });
      changeTab('pet');
      const completed = await waitForTask(task.taskId);
      await completeTask(completed);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function selectCandidate(candidateRecordId: string) {
    if (!petData) return;
    setBusy('正在保存选择…');
    setIssue(null);
    try {
      await api(`/v1/pet-candidates/${encodeURIComponent(petData.task.taskId)}/select`, {
        method: 'POST',
        body: JSON.stringify({
          candidateRecordId,
          expectedSelectionVersion: petData.selection?.selectionVersion ?? 0,
        }),
        idempotentWrite: true,
      });
      await loadCandidates(petData.task.taskId);
      setNotice('桌宠选择已保存，四个动作预览已开始准备。');
    } catch (error) {
      report(error);
      if (error instanceof ApiError && error.code === 'SELECTION_CONFLICT') {
        await loadCandidates(petData.task.taskId).catch(() => undefined);
      }
    } finally {
      setBusy(null);
    }
  }

  async function activateDesktopPet(taskId: string) {
    setBusy('正在把桌宠放到桌面…');
    setIssue(null);
    try {
      const result = await api<{ activated: boolean; name: string; speciesName: string; pid: number }>(
        `/v1/pet-candidates/${encodeURIComponent(taskId)}/activate-desktop`,
        { method: 'POST' },
      );
      setDesktopPetAction('idle');
      setNotice(`桌宠「${result.name}」已出现在桌面，可以拖拽、单击和右键操作。`);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function setDesktopAction(taskId: string, action: DesktopPetAction) {
    setBusy(`正在切换为「${{ idle: '待机', listen: '听歌', dance: '跳舞', sleep: '睡觉' }[action]}」…`);
    setIssue(null);
    try {
      await api<{ accepted: boolean; action: DesktopPetAction; pid: number }>(
        `/v1/pet-candidates/${encodeURIComponent(taskId)}/desktop-state`,
        { method: 'POST', body: JSON.stringify({ action, say: true }) },
      );
      setDesktopPetAction(action);
      setNotice(`桌面上的桌宠已切换为「${{ idle: '待机', listen: '听歌', dance: '跳舞', sleep: '睡觉' }[action]}」。`);
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function exportCharacterCard() {
    if (!petData?.selection) return;
    const selected = petData.candidates.find(
      (candidate) => candidate.candidateRecordId === petData.selection?.candidateRecordId,
    );
    const filename = `${selected?.copy.petName ?? '桌宠'}-歌单角色卡.png`;
    setBusy('正在导出角色卡…');
    setIssue(null);
    try {
      const blob = await apiBlob(
        `/v1/pet-candidates/${encodeURIComponent(petData.task.taskId)}/cards/${encodeURIComponent(petData.selection.candidateRecordId)}`,
      );
      await svgBlobToPngDownload(blob, filename);
      setNotice('角色卡已保存为 PNG。');
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function loadPetActions(taskId: string, candidateId: string) {
    const result = await api<PetActionsResponse>(
      `/v1/pet-candidates/${encodeURIComponent(taskId)}/actions/${encodeURIComponent(candidateId)}`,
    );
    setPetActionsFallbackMode(result.fallbackMode);
    if (result.actions.length > 0) {
      const entries = await Promise.all(
        result.actions.map(async (item) => {
          const blob = await apiBlob(item.previewUrl);
          return [item.action, URL.createObjectURL(blob)] as const;
        }),
      );
      actionUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      actionUrlsRef.current = entries.map(([, url]) => url);
      setActionUrls(Object.fromEntries(entries));
    }
    return result;
  }

  useEffect(() => {
    const candidateId = petData?.selection?.candidateRecordId;
    const taskId = petData?.task.taskId;
    if (!candidateId || !taskId) return;
    let cancelled = false;
    let attempts = 0;
    actionUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    actionUrlsRef.current = [];
    window.queueMicrotask(() => {
      if (cancelled) return;
      setActionUrls({});
      setPetActionLoadState('loading');
    });
    const poll = async () => {
      attempts += 1;
      try {
        const result = await loadPetActions(taskId, candidateId);
        if (cancelled) return;
        if (result.actions.length >= 4 && result.generationState === 'ready') {
          setPetActionLoadState('ready');
          return;
        }
        if (result.generationState === 'failed' || attempts >= 15) {
          setPetActionLoadState('fallback');
          return;
        }
      } catch {
        if (cancelled) return;
        if (attempts >= 5) {
          setPetActionLoadState('fallback');
          return;
        }
      }
      actionsPollRef.current = setTimeout(poll, 800);
    };
    void poll();
    return () => {
      cancelled = true;
      if (actionsPollRef.current) clearTimeout(actionsPollRef.current);
    };
  }, [petData?.selection?.candidateRecordId, petData?.task.taskId, petActionsRefresh]);

  async function revokeConsent() {
    if (!consentRecord) return;
    setConfirmAction(null);
    setBusy('正在撤回授权…');
    setIssue(null);
    try {
      const revoked = await api<ConsentRecord>(
        `/v1/consents/${encodeURIComponent(consentRecord.consentId)}/revoke`,
        { method: 'POST', idempotentWrite: true },
      );
      setConsentRecord(revoked);
      setNotice('外部处理授权已撤回；本地规则结果仍可查看。');
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function deleteImport() {
    const importId = importRecord?.importId ?? flow.importId;
    if (!importId) return;
    setConfirmAction(null);
    setBusy('正在提交删除请求…');
    setIssue(null);
    try {
      let result = await api<DeletionRecord>(
        `/v1/playlist-imports/${encodeURIComponent(importId)}`,
        { method: 'DELETE', idempotentWrite: true },
      );
      setDeletion(result);
      patchFlow({ deletionId: result.deletionId });
      for (let attempt = 0; attempt < 75 && result.status !== 'DELETION_COMPLETED'; attempt += 1) {
        if (result.status === 'FINAL_FAILED' || result.status === 'RETRYABLE_FAILED') break;
        await sleep(800);
        result = await api<DeletionRecord>(`/v1/deletions/${encodeURIComponent(result.deletionId)}`);
        setDeletion(result);
      }
      if (result.status === 'DELETION_COMPLETED') {
        clearWorkflowAfterDeletion();
        setNotice('这份歌单及其画像、候选和授权记录已从后端删除。');
      } else {
        setIssue({ code: result.errorCode ?? 'DELETION_PENDING', message: '删除尚未完成，请稍后刷新状态。' });
      }
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  async function refreshDeletion() {
    if (!deletion) return;
    setBusy('正在查询删除状态…');
    try {
      const result = await api<DeletionRecord>(`/v1/deletions/${encodeURIComponent(deletion.deletionId)}`);
      setDeletion(result);
      if (result.status === 'DELETION_COMPLETED') {
        clearWorkflowAfterDeletion();
        setNotice('后端数据已删除。');
      }
    } catch (error) {
      report(error);
    } finally {
      setBusy(null);
    }
  }

  const moodName = (moodId: string) => catalog.find((item) => item.moodId === moodId)?.displayName ?? moodId;
  const pendingCount = Object.keys(pendingCorrections).length;
  const profileBlocked = profile
    ? profile.knownCount < 10 || profile.unknownRate > 0.5
    : true;

  return (
    <section className="melopet-window flex flex-col overflow-hidden sm:rounded-[10px]" aria-label="Playlist Pet 桌宠">
      <header className="flex items-center justify-between border-b bg-[#181B32] px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-[#1E2138] text-[#8B7CFF]">
            <Music2 aria-hidden="true" className="size-5" />
          </span>
          <div className="min-w-0">
            <p className="text-[10px] font-semibold tracking-[0.14em] text-[#A8ACC4]">PLAYLIST PET</p>
            <h1 className="truncate text-[17px] font-bold tracking-tight">把歌单，变成一只桌宠</h1>
          </div>
        </div>
        <span className={`flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${
          serviceState === 'online'
            ? 'bg-[#17342B] text-[#7EE6C4]'
            : serviceState === 'offline'
              ? 'bg-[#3A2430] text-[#FF7B9C]'
              : 'bg-[#1E2138] text-[#A8ACC4]'
        }`}>
          {serviceState === 'online' ? <Wifi aria-hidden="true" className="size-3" /> : serviceState === 'offline' ? <WifiOff aria-hidden="true" className="size-3" /> : <Spinner className="size-3" />}
          {serviceState === 'online' ? '后端在线' : serviceState === 'offline' ? '后端离线' : '连接中'}
        </span>
      </header>

      <Tabs value={tab} onValueChange={(value) => changeTab(value as AppTab)} className="min-h-0 flex-1 gap-0">
        <TabsList variant="line" className="grid h-12 w-full grid-cols-5 border-b bg-[#181B32] px-3">
          <TabsTrigger value="home"><Home aria-hidden="true" />首页</TabsTrigger>
          <TabsTrigger value="import"><FileMusic aria-hidden="true" />导入</TabsTrigger>
          <TabsTrigger value="profile"><HeartPulse aria-hidden="true" />画像</TabsTrigger>
          <TabsTrigger value="pet"><Sparkles aria-hidden="true" />桌宠</TabsTrigger>
          <TabsTrigger value="privacy"><ShieldCheck aria-hidden="true" />我的</TabsTrigger>
        </TabsList>

        <div className="melopet-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
          {busy && (
            <output className="mb-3 flex items-center gap-2 rounded-xl border border-[#2B2F4A] bg-[#1E2138] px-3 py-2 text-sm text-[#F7F7FB]" aria-live="polite">
              <Spinner />{busy}
            </output>
          )}
          {issue && (
            <Alert variant="destructive" className="mb-3 border-[#4A2B3A] bg-[#2A1E2A]">
              <AlertCircle aria-hidden="true" />
              <AlertTitle>{issue.code}</AlertTitle>
              <AlertDescription>
                {issue.message}
                {issue.requestId && <span className="mt-1 block text-xs">请求号：{issue.requestId}</span>}
                {issue.code === 'DUPLICATE_IMPORT' && (
                  <Button size="xs" variant="outline" className="mt-2" onClick={() => void handleImport(true)}>仍要新建副本</Button>
                )}
                {issue.code === 'UNAUTHORIZED' && (
                  <Button size="xs" variant="outline" className="mt-2" onClick={() => {
                    clearStoredToken();
                    clearWorkflowAfterDeletion();
                    setIssue(null);
                    setNotice('无效本地身份已清除；下次提交时会自动创建新身份。');
                  }}>创建新的本地身份</Button>
                )}
              </AlertDescription>
            </Alert>
          )}
          {notice && (
            <Alert className="mb-3 border-[#1F4A3B] bg-[#17342B] text-[#7EE6C4]">
              <Check aria-hidden="true" />
              <AlertTitle>已完成</AlertTitle>
              <AlertDescription className="text-[#7EE6C4]">{notice}</AlertDescription>
            </Alert>
          )}

          <TabsContent value="home" className="space-y-6">
            <div className="px-2 pt-12 text-center">
              <p className="text-[11px] font-semibold tracking-[0.24em] text-[#8B7CFF]">PLAYLIST PET</p>
              <h2 className="mt-4 text-[34px] font-bold leading-[1.2] tracking-tight">
                把你的<span className="text-[#8B7CFF]">歌单</span>，<br />变成一只<span className="text-[#6EC8FF]">桌宠</span>
              </h2>
              <p className="mx-auto mt-4 max-w-[280px] text-sm leading-6 text-[#A8ACC4]">
                AI 会分析你的音乐偏好、情绪和节奏，为你生成一只独一无二的桌面伙伴。
              </p>
              <Button className="mt-9 h-[52px] w-full rounded-2xl text-[15px]" onClick={() => changeTab('import')}>
                <Sparkles aria-hidden="true" /> 开始生成
              </Button>
            </div>
            <div className="space-y-2">
              {[
                ['🎵', '听懂歌单 DNA', '分析流派、情绪、能量与关键词'],
                ['🐾', '生成专属桌宠', '物种、配色、动作随歌单变化'],
                ['💜', '住进你的桌面', '透明置顶、拖拽、待机陪伴'],
              ].map(([icon, title, desc]) => (
                <div key={title} className="flex items-start gap-3 rounded-2xl bg-[#181B32] p-4 ring-1 ring-[#2B2F4A]">
                  <span className="text-xl" aria-hidden="true">{icon}</span>
                  <div>
                    <p className="text-sm font-semibold">{title}</p>
                    <p className="mt-0.5 text-xs leading-5 text-[#A8ACC4]">{desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </TabsContent>

          <TabsContent value="import" className="space-y-4">
            <SectionHeading eyebrow="第 1 步 · 导入歌曲" title="先让我听懂你的歌单" description="一次选择或拖入多首音频文件，系统会自动整理歌曲信息。" />

            {!importRecord ? (
              <>
                <Card className="border-0 shadow-[0_10px_30px_rgb(0_0_0/40%)] ring-1 ring-[#2B2F4A]">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-2">
                    选择歌曲
                    {audioSongs.length > 0 && <Badge variant="secondary">{audioSongs.length} 首</Badge>}
                  </CardTitle>
                  <CardDescription>最多 1,000 首，支持 MP3、M4A、FLAC、WAV、OGG 等常见音频。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <input
                    ref={audioInputRef}
                    type="file"
                    accept={AUDIO_ACCEPT}
                    multiple
                    className="sr-only"
                    disabled={Boolean(parseProgress)}
                    onChange={(event) => {
                      void handleAudioFiles(Array.from(event.currentTarget.files ?? []));
                      event.currentTarget.value = '';
                    }}
                  />
                  <button
                    type="button"
                    aria-label="选择多首音频文件"
                    disabled={Boolean(parseProgress)}
                    className="grid min-h-40 w-full cursor-pointer place-items-center rounded-xl border border-dashed border-[#2B2F4A] bg-[#181B32] p-5 text-center outline-none transition-colors hover:bg-[#1E2138] focus-visible:ring-3 focus-visible:ring-ring/40 disabled:cursor-wait disabled:opacity-70"
                    onClick={() => !parseProgress && audioInputRef.current?.click()}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => {
                      event.preventDefault();
                      if (!parseProgress) void handleAudioFiles(Array.from(event.dataTransfer.files));
                    }}
                  >
                    <span>
                      <UploadCloud className="mx-auto mb-3 size-8 text-[#8B7CFF]" aria-hidden="true" />
                      <span className="block font-semibold">点击选择多首歌曲</span>
                      <span className="mt-1 block text-xs text-muted-foreground">也可把多个音频文件一起拖到这里</span>
                    </span>
                  </button>

                  {parseProgress && (
                    <Progress value={Math.round((parseProgress.done / parseProgress.total) * 100)}>
                      <ProgressLabel>
                        正在并行读取标签与音频特征 {parseProgress.done}/{parseProgress.total}
                        {parseProgress.cached > 0 ? ` · 缓存 ${parseProgress.cached}` : ''}
                      </ProgressLabel>
                    </Progress>
                  )}

                  {audioSongs.length > 0 && (
                    <div className="space-y-2 rounded-xl border bg-[#181B32] p-2">
                      <div className="flex items-center justify-between gap-2 px-1 py-1">
                        <span className="text-sm font-semibold">已选 {audioSongs.length} 首</span>
                        <div className="flex gap-1">
                          <Button size="xs" variant="ghost" disabled={Boolean(parseProgress)} onClick={() => audioInputRef.current?.click()}>继续添加</Button>
                          <Button size="xs" variant="ghost" disabled={Boolean(parseProgress)} onClick={() => {
                            setAudioSongs([]);
                            setNotice(null);
                          }}>清空</Button>
                        </div>
                      </div>
                      <div className="melopet-scrollbar max-h-72 space-y-1.5 overflow-y-auto" aria-label="已选歌曲列表">
                        {audioSongs.map((song, index) => (
                          <div key={song.id} className="flex items-center gap-3 rounded-lg bg-[#1E2138] p-2.5">
                            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[#1E2138] text-xs font-semibold text-[#8B7CFF]">{index + 1}</span>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium">{song.title}</p>
                              <p className="truncate text-xs text-muted-foreground">
                                {song.artist} · {formatDuration(song.durationMs)} · {formatFileSize(song.size)}
                              </p>
                              {song.warning && <p className="mt-0.5 truncate text-[11px] text-[#FFB078]">{song.warning}</p>}
                            </div>
                            <Badge variant={song.audioFeatures || song.usedEmbeddedTags ? 'secondary' : 'outline'} className="shrink-0 text-[10px]">
                              {song.audioAnalysisSource === 'cache'
                                ? '特征缓存'
                                : song.audioAnalysisSource === 'computed'
                                  ? '音频分析'
                                  : song.usedEmbeddedTags ? '已读标签' : '文件名'}
                            </Badge>
                            <Button
                              size="icon-xs"
                              variant="ghost"
                              aria-label={`移除 ${song.title}`}
                              disabled={Boolean(parseProgress)}
                              onClick={() => setAudioSongs((current) => current.filter((item) => item.id !== song.id))}
                            >
                              <Trash2 aria-hidden="true" />
                            </Button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <Alert className="border-[#2B2F4A] bg-[#181B32] text-[#F7F7FB]">
                    <ShieldCheck aria-hidden="true" />
                    <AlertTitle>音频原文件不会上传</AlertTitle>
                    <AlertDescription className="text-[#A8ACC4]">
                      浏览器在本机读取标签并分段分析能量、频谱亮度、动态和节拍；只向本地后端提交元数据与数值特征。
                    </AlertDescription>
                  </Alert>

                  <label htmlFor="consent-choice" className="flex cursor-pointer items-start gap-3 rounded-xl bg-[#1E2138] p-3 text-sm leading-5">
                    <Checkbox id="consent-choice" checked={consentChoice} onCheckedChange={(value) => setConsentChoice(value === true)} aria-label="允许外部模型处理必要字段" />
                    <span>
                      <span className="block font-medium">允许外部模型处理必要字段</span>
                      <span className="block text-xs text-muted-foreground">不勾选也可走本地规则；授权可随时撤回。</span>
                    </span>
                  </label>
                  <Button className="h-11 w-full rounded-full text-[15px]" disabled={Boolean(busy) || Boolean(parseProgress) || audioSongs.length === 0} onClick={() => void handleImport()}>
                    {audioSongs.length > 0 ? `导入 ${audioSongs.length} 首歌曲` : '请先选择歌曲'}
                  </Button>
                </CardContent>
              </Card>

                <Card className="ring-[#2B2F4A]">
                  <CardHeader>
                    <CardTitle>粘贴歌单 / 上传 CSV</CardTitle>
                    <CardDescription>可直接粘贴网易云歌单链接，或复制歌单文本、上传 CSV/TXT。纯文本支持每行「歌名 - 歌手」，也支持带表头的表格。</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <textarea
                      value={playlistText}
                      onChange={(event) => setPlaylistText(event.target.value)}
                      placeholder="粘贴网易云歌单链接，或每行一条「歌名 - 歌手」，例如：晴天 - 周杰伦"
                      rows={6}
                      className="w-full resize-y rounded-xl border border-[#2B2F4A] bg-[#181B32] p-3 text-sm outline-none transition-colors focus:border-[#8B7CFF] focus:ring-2 focus:ring-[#8B7CFF]/20"
                    />
                    <Button className="h-11 w-full rounded-full text-[15px]" disabled={Boolean(busy)} onClick={() => void handlePlaylistTextImport()}>
                      粘贴导入
                    </Button>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="h-px flex-1 bg-[#2B2F4A]" />
                      或上传文件
                      <span className="h-px flex-1 bg-[#2B2F4A]" />
                    </div>
                    <input
                      ref={playlistFileRef}
                      type="file"
                      accept=".csv,.txt,text/csv,text/plain"
                      className="sr-only"
                      onChange={(event) => {
                        void handlePlaylistFile(Array.from(event.currentTarget.files ?? []));
                      }}
                    />
                    <Button className="h-11 w-full rounded-full text-[15px]" variant="outline" disabled={Boolean(busy)} onClick={() => playlistFileRef.current?.click()}>
                      上传 CSV / TXT
                    </Button>
                  </CardContent>
                </Card>
              </>
            ) : (
              <>
                {!playlist && (
                  <Card className="ring-[#2B2F4A]">
                    <CardHeader>
                      <CardTitle>歌曲已收到</CardTitle>
                      <CardDescription>后端还在等待整理歌曲信息，可以手动继续。</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <Button className="w-full" disabled={Boolean(busy)} onClick={() => {
                        setBusy('正在整理歌曲信息…');
                        setIssue(null);
                        void normalizeImportedSongs(importRecord)
                          .then((mapped) => setNotice(`已规范化 ${mapped.normalizedCount} 首歌曲。`))
                          .catch(report)
                          .finally(() => setBusy(null));
                      }}>继续整理</Button>
                    </CardContent>
                  </Card>
                )}

                {playlist && (
                  <Card className="ring-[#2B2F4A]">
                    <CardHeader>
                      <CardTitle>规范化结果</CardTitle>
                      <CardDescription>下方最多预览前 20 行，不展示未授权字段。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="grid grid-cols-3 gap-2">
                        {[
                          ['有效', playlist.normalizedCount, '#7EE6C4'],
                          ['无效', playlist.invalidCount, '#FFB078'],
                          ['重复', playlist.duplicateCount, '#8B7CFF'],
                        ].map(([label, value, color]) => (
                          <div key={String(label)} className="rounded-xl bg-[#1E2138] p-3 text-center">
                            <strong className="block text-xl" style={{ color: String(color) }}>{String(value)}</strong>
                            <span className="text-xs text-muted-foreground">{String(label)}</span>
                          </div>
                        ))}
                      </div>
                      {playlist.lowSampleTrial && (
                        <Alert className="border-[#2B2F4A] bg-[#1E2138]">
                          <Info aria-hidden="true" /><AlertTitle>当前为试玩画像</AlertTitle>
                          <AlertDescription>有效歌曲少于 10 首，可查看画像，但不能确认或生成正式候选。</AlertDescription>
                        </Alert>
                      )}
                      <div className="rounded-xl border bg-[#181B32]">
                        <Table>
                          <TableHeader><TableRow><TableHead>歌曲</TableHead><TableHead>艺人</TableHead></TableRow></TableHeader>
                          <TableBody>
                            {playlist.preview.map((track) => (
                              <TableRow key={track.trackRef}>
                                <TableCell className="max-w-40 truncate font-medium">{track.title}</TableCell>
                                <TableCell className="max-w-28 truncate text-muted-foreground">{track.artist}</TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                      <Button className="h-11 w-full" disabled={Boolean(busy) || playlist.normalizedCount < 1} onClick={() => void handleAnalysis()}>
                        生成情绪画像
                      </Button>
                    </CardContent>
                  </Card>
                )}

                <Button variant="ghost" className="w-full" onClick={() => changeTab('privacy')}>到“我的”删除后换一批歌曲</Button>
              </>
            )}
            <p className="text-center text-xs leading-5 text-muted-foreground">音频文件留在你的电脑上；提交的歌曲信息和结果可在“我的”中删除。</p>
          </TabsContent>

          <TabsContent value="profile" className="space-y-4">
            {!profile ? (
              <>
                {activeTask && activeTask.taskType === 'ANALYZE_PLAYLIST' ? (
                  <TaskPanel task={activeTask} busy={Boolean(busy)} onRefresh={refreshActiveTask} onCancel={cancelActiveTask} onRetry={retryActiveTask} />
                ) : (
                  <EmptyState
                    icon={<HeartPulse className="size-7" aria-hidden="true" />}
                    title="画像还没生成"
                    description="导入歌曲后，AI 会自动分析每首歌的风格，这里会显示总和为 100% 的情绪分布。"
                    action={<Button variant="outline" onClick={() => changeTab('import')}>前往导入</Button>}
                  />
                )}
              </>
            ) : (
              <>
                <SectionHeading eyebrow={`第 2 步 · 画像版本 ${profile.revisionNumber}`} title="看懂这份歌单的 DNA" description="画像描述的是歌曲氛围，不用于判断你的真实性格或心理状态。" />

                {profile.dna && (
                  <Card className="ring-[#2B2F4A]">
                    <CardHeader>
                      <CardTitle>歌单 DNA</CardTitle>
                      <CardDescription className="leading-6">{profile.dna.summary}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div>
                        <div className="mb-1.5 flex items-center justify-between text-sm">
                          <span className="font-medium">能量值</span>
                          <span className="tabular-nums text-muted-foreground">{profile.dna.energy}/100</span>
                        </div>
                        <Progress value={profile.dna.energy} aria-label="能量值" className="[&_[data-slot=progress-indicator]]:bg-[#8B7CFF]" />
                      </div>
                      <div>
                        <p className="mb-1.5 text-sm font-medium">主流派</p>
                        <div className="flex flex-wrap gap-1.5">
                          {profile.dna.genres.map((genre) => <Badge key={genre} variant="secondary">{genre}</Badge>)}
                        </div>
                      </div>
                      <div>
                        <p className="mb-1.5 text-sm font-medium">审美关键词</p>
                        <div className="flex flex-wrap gap-1.5">
                          {profile.dna.keywords.map((keyword) => <Badge key={keyword} variant="outline">{keyword}</Badge>)}
                        </div>
                      </div>
                      {profile.dna.representativeTracks.length > 0 && (
                        <div>
                          <p className="mb-1.5 text-sm font-medium">代表歌曲</p>
                          <div className="space-y-1 rounded-xl bg-[#1E2138] p-3">
                            {profile.dna.representativeTracks.map((track) => (
                              <p key={`${track.title}-${track.artist}`} className="truncate text-xs text-muted-foreground">
                                《{track.title}》{track.artist ? ` · ${track.artist}` : ''}
                              </p>
                            ))}
                          </div>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                <Card className="ring-[#2B2F4A]">
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between gap-2">
                      情绪分布
                      {profile.confirmedAt ? <Badge className="bg-[#17342B] text-[#7EE6C4]">已确认</Badge> : <Badge variant="secondary">待确认</Badge>}
                    </CardTitle>
                    <CardDescription>按有效、未排除歌曲计算，百分比合计 100%。</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {profile.percentages.map((item) => (
                      <div key={item.moodId}>
                        <div className="mb-1.5 flex items-center justify-between text-sm">
                          <span className="font-medium">{moodName(item.moodId)}</span>
                          <span className="tabular-nums text-muted-foreground">{item.percent}%</span>
                        </div>
                        <Progress value={item.percent} aria-label={`${moodName(item.moodId)} ${item.percent}%`} className="[&_[data-slot=progress-indicator]]:bg-[#8B7CFF]" />
                      </div>
                    ))}
                    <div className="grid grid-cols-3 gap-2 pt-1 text-center">
                      <div className="rounded-lg bg-[#17342B] p-2"><strong className="block text-[#7EE6C4]">{profile.knownCount}</strong><span className="text-[11px] text-muted-foreground">已识别</span></div>
                      <div className="rounded-lg bg-[#1E2138] p-2"><strong className="block text-[#FFB078]">{profile.unknownCount}</strong><span className="text-[11px] text-muted-foreground">待确认</span></div>
                      <div className="rounded-lg bg-[#1E2138] p-2"><strong className="block text-[#A8ACC4]">{profile.excludedCount}</strong><span className="text-[11px] text-muted-foreground">已排除</span></div>
                    </div>
                  </CardContent>
                </Card>

                {profile.qualityFlags.length > 0 && (
                  <Alert className="border-[#2B2F4A] bg-[#1E2138]">
                    <CircleHelp aria-hidden="true" />
                    <AlertTitle>结果提示</AlertTitle>
                    <AlertDescription>{profile.qualityFlags.map((flag) => QUALITY_LABELS[flag] ?? flag).join('；')}</AlertDescription>
                  </Alert>
                )}

                {!profile.confirmedAt && profile.tracks.length > 0 && (
                  <Card className="ring-[#2B2F4A]">
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between gap-2">
                        逐曲纠正
                        {pendingCount > 0 && <Badge variant="secondary">{pendingCount} 处待保存</Badge>}
                      </CardTitle>
                      <CardDescription>为「待确认」歌曲手动指定氛围，或排除不适合的歌曲；保存后生成新的画像版本。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="melopet-scrollbar max-h-96 space-y-2 overflow-y-auto">
                        {profile.tracks.map((track) => {
                          const pending = pendingCorrections[track.trackRef];
                          const effectiveMoodId = pending?.action === 'set_mood' && pending.moodId
                            ? pending.moodId
                            : pending?.action === 'restore_auto'
                              ? track.autoMoodId
                              : track.finalMoodId;
                          const excluded = pending?.action === 'exclude'
                            ? true
                            : (pending?.action === 'include' || pending?.action === 'set_mood' || pending?.action === 'restore_auto')
                              ? false
                              : track.excluded;
                          const selectValue = effectiveMoodId === 'UNKNOWN' ? '' : effectiveMoodId;
                          return (
                            <div key={track.trackRef} className={`rounded-xl bg-[#1E2138] p-3 ${excluded ? 'opacity-60' : ''}`}>
                              <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                  <p className="truncate text-sm font-medium">{track.title}</p>
                                  <p className="truncate text-xs text-muted-foreground">{track.artist} · 置信度 {track.confidenceBand}</p>
                                </div>
                                <div className="flex shrink-0 items-center gap-1.5">
                                  {track.correctionSource === 'user' && <Badge variant="outline">已纠正</Badge>}
                                  <Badge variant={excluded ? 'outline' : effectiveMoodId === 'UNKNOWN' ? 'destructive' : 'secondary'}>
                                    {excluded ? '已排除' : moodName(effectiveMoodId)}
                                  </Badge>
                                </div>
                              </div>
                              <div className="mt-2 flex items-center gap-2">
                                <select
                                  aria-label={`设置《${track.title}》的氛围`}
                                  value={selectValue}
                                  disabled={Boolean(busy)}
                                  onChange={(event) => {
                                    const moodId = event.target.value;
                                    if (!moodId) {
                                      clearPendingCorrection(track.trackRef);
                                    } else {
                                      setPendingCorrections((current) => ({ ...current, [track.trackRef]: { action: 'set_mood', moodId } }));
                                    }
                                  }}
                                  className="h-9 min-w-0 flex-1 rounded-lg border border-[#2B2F4A] bg-[#181B32] px-2 text-xs text-[#F7F7FB] outline-none focus:border-[#8B7CFF]"
                                >
                                  <option value="">{track.finalMoodId === 'UNKNOWN' ? '选择氛围…' : '保持自动识别'}</option>
                                  {catalog.filter((mood) => mood.moodId !== 'UNKNOWN').map((mood) => (
                                    <option key={mood.moodId} value={mood.moodId}>{mood.displayName}</option>
                                  ))}
                                </select>
                                <Button size="xs" variant={excluded ? 'secondary' : 'outline'} disabled={Boolean(busy)} onClick={() => toggleTrackExclusion(track)}>
                                  {excluded ? '纳入' : '排除'}
                                </Button>
                                {track.correctionSource === 'user' && (
                                  <Button size="xs" variant="ghost" disabled={Boolean(busy)} onClick={() => setPendingCorrections((current) => ({ ...current, [track.trackRef]: { action: 'restore_auto' } }))}>
                                    恢复自动
                                  </Button>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      <Button className="h-11 w-full" disabled={Boolean(busy) || pendingCount === 0} onClick={() => void applyCorrections()}>
                        保存纠正{pendingCount > 0 ? `（${pendingCount} 处）` : ''}
                      </Button>
                    </CardContent>
                  </Card>
                )}

                <Card className="ring-[#2B2F4A]">
                  <CardContent className="space-y-3 pt-4">
                    {!profile.confirmedAt ? (
                      <>
                        <Button className="h-11 w-full" disabled={Boolean(busy) || profileBlocked || pendingCount > 0} onClick={() => void confirmProfile()}>确认这版画像</Button>
                        {profile.knownCount < 10 && <p className="text-center text-xs text-[#FFB078]">至少需要 10 首已识别歌曲才能确认。</p>}
                        {profile.unknownRate > 0.5 && <p className="text-center text-xs text-[#FFB078]">待确认歌曲超过一半，请先纠正。</p>}
                        {pendingCount > 0 && <p className="text-center text-xs text-[#FFB078]">还有 {pendingCount} 处纠正未保存，请先保存。</p>}
                      </>
                    ) : (
                      <>
                        {petCatalog ? (
                          <div className="space-y-3 rounded-xl bg-[#1E2138] p-3 text-left">
                            <div className="flex items-center justify-between">
                              <p className="text-xs font-semibold text-[#8B7CFF]">定制你的桌宠</p>
                              {Object.keys(petCustomization).length > 0 && (
                                <Button size="xs" variant="ghost" onClick={() => setPetCustomization({})}>恢复自动</Button>
                              )}
                            </div>
                            <div>
                              <p className="mb-1.5 text-xs text-muted-foreground">宠物类型</p>
                              <div className="flex flex-wrap gap-1.5">
                                {petCatalog.species.map((item) => (
                                  <Button key={item.id} size="xs" variant={petCustomization.speciesId === item.id ? 'default' : 'outline'} onClick={() => setPetCustomization((current) => ({ ...current, speciesId: item.id }))}>{item.name}</Button>
                                ))}
                              </div>
                            </div>
                            <div>
                              <p className="mb-1.5 text-xs text-muted-foreground">主色</p>
                              <div className="flex flex-wrap gap-2">
                                {petCatalog.palettes.map((item) => (
                                  <button key={item.id} type="button" title={item.name} aria-label={item.name} onClick={() => setPetCustomization((current) => ({ ...current, paletteId: item.id }))} className={`size-7 rounded-full border-2 ${petCustomization.paletteId === item.id ? 'border-[#8B7CFF] ring-2 ring-[#8B7CFF]/30' : 'border-[#2B2F4A]'}`} style={{ background: `linear-gradient(135deg, ${item.colors[0] ?? '#3A3F5C'}, ${item.colors[1] ?? item.colors[0] ?? '#3A3F5C'})` }} />
                                ))}
                              </div>
                            </div>
                            <div>
                              <p className="mb-1.5 text-xs text-muted-foreground">风格 / 动作</p>
                              <div className="flex flex-wrap gap-1.5">
                                {petCatalog.motions.map((item) => (
                                  <Button key={item.id} size="xs" variant={petCustomization.motionId === item.id ? 'default' : 'outline'} onClick={() => setPetCustomization((current) => ({ ...current, motionId: item.id }))}>{item.name}</Button>
                                ))}
                              </div>
                            </div>
                            <label htmlFor="pet-headphone-choice" className="flex cursor-pointer items-center gap-2 text-sm">
                              <Checkbox id="pet-headphone-choice" checked={petCustomization.headphone === true} onCheckedChange={(value) => setPetCustomization((current) => ({ ...current, headphone: value === true }))} aria-label="佩戴耳机" />
                              <span>佩戴耳机</span>
                            </label>
                          </div>
                        ) : (
                          <p className="text-center text-xs text-muted-foreground">正在加载定制选项…</p>
                        )}
                        <Button className="h-11 w-full" disabled={Boolean(busy)} onClick={() => void generatePets()}><Sparkles aria-hidden="true" />生成我的桌宠</Button>
                      </>
                    )}
                  </CardContent>
                </Card>
              </>
            )}
          </TabsContent>

          <TabsContent value="pet" className="space-y-4">
            {!petData || petData.candidates.length === 0 ? (
              <>
                {activeTask && activeTask.taskType === 'GENERATE_PET_CANDIDATES' ? (
                  <TaskPanel task={activeTask} busy={Boolean(busy)} onRefresh={refreshActiveTask} onCancel={cancelActiveTask} onRetry={retryActiveTask} />
                ) : (
                  <EmptyState
                    icon={<Sparkles className="size-7" aria-hidden="true" />}
                    title="等待你的桌宠"
                    description="确认情绪画像后，会生成 3 个来自真实后端渲染器的候选。"
                    action={<Button variant="outline" onClick={() => changeTab('profile')}>查看画像</Button>}
                  />
                )}
              </>
            ) : (
              <>
                <SectionHeading eyebrow="第 3 步 · 选择候选" title="哪只桌宠更像这份歌单？" description="形态由固定映射规则决定；短文案可能来自模型或安全模板。" />
                {profile?.dna && (
                  <div className="rounded-xl bg-[#1E2138] p-3">
                    <p className="mb-1 text-xs font-semibold text-[#8B7CFF]">来自这份歌单的 DNA</p>
                    <p className="text-sm leading-6">{profile.dna.summary}</p>
                    <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                      <span>能量值 {profile.dna.energy}/100</span>
                      <span>·</span>
                      <span>{profile.dna.keywords.slice(0, 3).join(' / ')}</span>
                    </div>
                  </div>
                )}
                {petData.candidates.map((candidate) => {
                  const selected = petData.selection?.candidateRecordId === candidate.candidateRecordId;
                  return (
                    <Card key={candidate.candidateRecordId} className={`ring-[#2B2F4A] ${selected ? 'ring-2 ring-[#8B7CFF]' : ''}`}>
                      <div className="relative mx-4 overflow-hidden rounded-2xl bg-[#1E2138]">
                        {previewUrls[candidate.candidateRecordId] ? (
                          <Image unoptimized src={previewUrls[candidate.candidateRecordId]} alt={`${candidate.copy.petName} 桌宠候选预览`} width={360} height={360} className="aspect-square w-full object-cover" />
                        ) : (
                          <div className="grid aspect-square place-items-center"><Spinner className="size-7 text-[#8B7CFF]" /></div>
                        )}
                        {selected && <Badge className="absolute right-3 top-3 bg-[#7EE6C4]">已选择</Badge>}
                      </div>
                      <CardHeader>
                        <CardTitle className="text-xl">{candidate.copy.petName}</CardTitle>
                        <CardDescription className="leading-6">{candidate.copy.intro}</CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <div className="rounded-xl bg-[#1E2138] p-3">
                          <p className="mb-2 text-xs font-semibold text-[#8B7CFF]">为什么是这个样子</p>
                          {candidate.petSpec.traitBindings.map((binding) => (
                            <p key={binding.traitId} className="text-xs leading-5 text-muted-foreground">
                              <span className="font-medium text-foreground">{binding.traitId === 'bodyPaletteId' ? '身体配色' : binding.traitId === 'accessoryIds' ? '随身配饰' : binding.traitId}</span>
                              {' '}来自 {binding.derivedFrom.map((item) => {
                                const [id, percent] = item.split(':');
                                return `${moodName(id)} ${percent}%`;
                              }).join('、')}
                            </p>
                          ))}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          <Badge variant="outline">动作：{candidate.petSpec.idleMotionId.replace(/_dev$/, '').replace('motion_', '')}</Badge>
                          {candidate.petSpec.customized && candidate.petSpec.customized.length > 0 && <Badge variant="secondary">已按你的偏好调整</Badge>}
                          {candidate.copy.promptVersion.startsWith('fixed-template') && <Badge variant="secondary">安全模板文案</Badge>}
                          {candidate.petSpec.developmentOnly && <Badge variant="destructive">开发素材</Badge>}
                        </div>
                        <Button className="h-10 w-full" variant={selected ? 'secondary' : 'default'} disabled={Boolean(busy) || selected} onClick={() => void selectCandidate(candidate.candidateRecordId)}>
                          {selected ? <><Check aria-hidden="true" />已保存这只桌宠</> : '选择这只桌宠'}
                        </Button>
                      </CardContent>
                    </Card>
                  );
                })}
                {petData.selection && (
                  <Card className="ring-[#2B2F4A]">
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between gap-2">角色卡
                        <Badge variant="secondary">可保存</Badge>
                      </CardTitle>
                      <CardDescription>歌单 DNA + 你的桌宠设定，一键保存 PNG 分享。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {cardUrl ? (
                        <Image unoptimized src={cardUrl} alt="桌宠角色卡" width={720} height={1000} className="w-full rounded-xl border border-[#2B2F4A]" />
                      ) : (
                        <div className="grid aspect-[720/1000] place-items-center rounded-xl bg-[#1E2138]"><Spinner className="size-7 text-[#8B7CFF]" /></div>
                      )}
                      <Button className="h-11 w-full" variant="outline" disabled={Boolean(busy)} onClick={() => void activateDesktopPet(petData.task.taskId)}><Monitor aria-hidden="true" />放到桌面</Button>
                      <Button className="h-11 w-full" disabled={Boolean(busy)} onClick={() => void exportCharacterCard()}><Download aria-hidden="true" />保存 PNG</Button>
                    </CardContent>
                  </Card>
                )}
                {petData.selection && (
                  <Card className="ring-[#2B2F4A]">
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between gap-2">桌宠动作
                        {petActionLoadState === 'ready' ? (
                          <Badge variant="secondary">{petActionsFallbackMode ? '本地预览' : '已生成'}</Badge>
                        ) : petActionLoadState === 'fallback' ? (
                          <Button size="xs" variant="outline" onClick={() => setPetActionsRefresh((value) => value + 1)}>
                            <RefreshCw aria-hidden="true" />重试
                          </Button>
                        ) : (
                          <Badge variant="outline">生成中…</Badge>
                        )}
                      </CardTitle>
                      <CardDescription>
                        {petActionLoadState === 'fallback'
                          ? '动作资源暂未完成，已展示基础预览，不会继续无限加载。'
                          : petActionsFallbackMode && petActionLoadState === 'ready'
                            ? '当前未启用图片模型，已使用本地安全预览完成四个状态。'
                            : 'Idle / Listen / Dance / Sleep 四个动态状态；点击卡片可切换桌面上的桌宠。'}
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="grid grid-cols-2 gap-2">
                        {(['idle', 'listen', 'dance', 'sleep'] as const).map((action) => {
                          const label = { idle: '待机', listen: '听歌', dance: '跳舞', sleep: '睡觉' }[action];
                          const url = actionUrls[action];
                          const fallbackUrl = previewUrls[petData.selection!.candidateRecordId];
                          const displayUrl = url ?? fallbackUrl;
                          return (
                            <button
                              key={action}
                              type="button"
                              disabled={Boolean(busy)}
                              title={`让桌面上的桌宠切换为${label}状态`}
                              onClick={() => void setDesktopAction(petData.task.taskId, action)}
                              className={`overflow-hidden rounded-xl bg-[#1E2138] text-left ring-1 transition hover:ring-[#8B7CFF] disabled:cursor-wait ${desktopPetAction === action ? 'ring-2 ring-[#8B7CFF]' : 'ring-[#2B2F4A]'}`}
                            >
                              <div className="relative aspect-square w-full">
                                {displayUrl ? (
                                  <Image unoptimized src={displayUrl} alt={`${label}动作`} width={256} height={256} className={`melopet-pet-motion melopet-pet-motion-${action} h-full w-full object-cover ${url ? '' : 'opacity-70'}`} />
                                ) : (
                                  <div className="grid h-full place-items-center"><Spinner className="size-5 text-[#8B7CFF]" /></div>
                                )}
                                {!url && displayUrl && (
                                  <span className="absolute right-2 top-2 rounded-full bg-[#11142A]/80 px-2 py-1 text-[10px] text-[#A8ACC4]">基础预览</span>
                                )}
                              </div>
                              <p className="py-1.5 text-center text-xs text-[#A8ACC4]">{label} · {action}{desktopPetAction === action ? ' · 当前' : ''}</p>
                            </button>
                          );
                        })}
                      </div>
                    </CardContent>
                  </Card>
                )}
                <Alert className="border-[#2B2F4A] bg-[#181B32]">
                  <Info aria-hidden="true" /><AlertTitle>M2 桌面动态已接入</AlertTitle>
                  <AlertDescription>点击「放到桌面」后，即使缩小或最小化浏览器，桌宠也会继续驻留；只有手动选择隐藏才会消失。桌宠支持拖拽、单击轮换动作，并可通过右键或菜单栏图标设置自动轮播、低动态、置顶、登录自启动、隐藏与退出。</AlertDescription>
                </Alert>
              </>
            )}
          </TabsContent>

          <TabsContent value="privacy" className="space-y-4">
            <SectionHeading eyebrow="数据与隐私" title="你的数据由你控制" description="查看当前授权、处理模式和删除状态。所有标识仅保存在这台设备。" />

            <Card className="ring-[#2B2F4A]">
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-2">运行状态
                  <Badge variant={serviceState === 'online' ? 'secondary' : 'destructive'}>{serviceState === 'online' ? '服务在线' : serviceState === 'offline' ? '服务离线' : '检查中'}</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex justify-between gap-4"><span className="text-muted-foreground">分析方式</span><span className="font-medium">{readiness?.ruleOnly ? '本地规则模式' : '模型 + 规则'}</span></div>
                <div className="flex justify-between gap-4"><span className="text-muted-foreground">候选素材</span><span className="font-medium">{readiness?.productionAssets ? '生产素材' : '开发验收素材'}</span></div>
                {!readiness?.productionReady && <p className="rounded-lg bg-[#1E2138] p-2 text-xs leading-5 text-[#FFB078]">当前环境用于开发验收，不代表真实模型和正式美术资产已接入。</p>}
              </CardContent>
            </Card>

            <Card className="ring-[#2B2F4A]">
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-2">外部处理授权
                  <Badge variant={consentRecord?.decision === 'allow' && !consentRecord.revokedAt ? 'secondary' : 'outline'}>
                    {consentRecord?.decision === 'allow' && !consentRecord.revokedAt ? '已允许' : '未允许'}
                  </Badge>
                </CardTitle>
                <CardDescription>{consentRecord ? `记录创建于 ${formatDate(consentRecord.createdAt)}` : '导入歌单后会生成一条授权决定记录。'}</CardDescription>
              </CardHeader>
              {consentRecord && (
                <CardContent className="space-y-3 text-sm">
                  <div className="rounded-xl bg-[#1E2138] p-3 text-xs leading-5 text-muted-foreground">
                    用途：{consentRecord.purpose}<br />
                    字段：{consentRecord.fields.join('、') || '无'}<br />
                    提供方：{consentRecord.provider} · 区域：{consentRecord.region}
                  </div>
                  {consentRecord.decision === 'allow' && !consentRecord.revokedAt && (
                    <Button variant="outline" className="w-full" disabled={Boolean(busy)} onClick={() => setConfirmAction('revoke')}>撤回外部处理授权</Button>
                  )}
                </CardContent>
              )}
            </Card>

            <Card className="ring-[#4A2B3A]">
              <CardHeader>
                <CardTitle>删除后端数据</CardTitle>
                <CardDescription>删除当前歌单、画像、候选预览和关联授权。此操作不可恢复。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {deletion && deletion.status !== 'DELETION_COMPLETED' && (
                  <div className="rounded-xl bg-[#1E2138] p-3 text-sm">
                    <p className="font-medium">{TASK_STATUS_LABELS[deletion.status] ?? deletion.status}</p>
                    <p className="mt-1 text-xs text-muted-foreground">删除请求：{deletion.deletionId}</p>
                    <Button size="sm" variant="outline" className="mt-2" onClick={() => void refreshDeletion()}><RefreshCw aria-hidden="true" />刷新状态</Button>
                  </div>
                )}
                <Button variant="destructive" className="w-full" disabled={(!importRecord && !flow.importId) || Boolean(busy)} onClick={() => setConfirmAction('delete')}><Trash2 aria-hidden="true" />{deletion && deletion.status !== 'DELETION_COMPLETED' ? '重试删除全部数据' : '删除当前歌单全部数据'}</Button>
              </CardContent>
            </Card>

            <p className="text-center text-xs leading-5 text-muted-foreground">匿名访问令牌只显示一次并保存在本机浏览器；丢失后无法找回原数据。</p>
          </TabsContent>
        </div>
      </Tabs>

      <AlertDialog open={confirmAction !== null} onOpenChange={(open) => { if (!open) setConfirmAction(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmAction === 'delete' ? '确定删除全部数据？' : '确定撤回授权？'}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction === 'delete'
                ? '当前歌单、画像、桌宠候选和关联授权会从后端删除，无法恢复。'
                : '后续任务将不再允许把必要字段发送给已配置的外部处理方；已有本地结果不会自动删除。'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>返回</AlertDialogCancel>
            <AlertDialogAction variant={confirmAction === 'delete' ? 'destructive' : 'default'} onClick={() => confirmAction === 'delete' ? void deleteImport() : void revokeConsent()}>
              {confirmAction === 'delete' ? '确认删除' : '确认撤回'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function TaskPanel({ task, busy, onRefresh, onCancel, onRetry }: {
  task: TaskRecord;
  busy: boolean;
  onRefresh: () => Promise<void>;
  onCancel: () => Promise<void>;
  onRetry: () => Promise<void>;
}) {
  const running = !TERMINAL_TASKS.has(task.status);
  return (
    <div className="space-y-4">
      <SectionHeading eyebrow="后台任务" title={TASK_STATUS_LABELS[task.status] ?? task.status} description="离开本页或刷新不会丢失进度，系统会通过任务编号继续恢复。" />
      <Card className="ring-[#2B2F4A]">
        <CardContent className="space-y-4 pt-4">
          <Progress value={task.progress}>
            <ProgressLabel>{TASK_STATUS_LABELS[task.status] ?? task.status}</ProgressLabel>
            <span className="ml-auto text-sm tabular-nums text-muted-foreground">{task.progress}%</span>
          </Progress>
          <div className="rounded-xl bg-[#1E2138] p-3 text-xs leading-5 text-muted-foreground">
            节点：{task.node}<br />任务号：{task.taskId}<br />最近更新：{formatDate(task.updatedAt)}
          </div>
          {task.safeMessage && <p className="text-sm text-destructive">{task.safeMessage}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" disabled={busy} onClick={() => void onRefresh()}><RefreshCw aria-hidden="true" />刷新状态</Button>
            {running && <Button variant="ghost" className="flex-1" disabled={busy} onClick={() => void onCancel()}>取消任务</Button>}
            {task.retryAllowed && <Button className="flex-1" disabled={busy} onClick={() => void onRetry()}>重试</Button>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
