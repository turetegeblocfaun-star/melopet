export type AppTab = 'home' | 'import' | 'profile' | 'pet' | 'privacy';

export type ImportRecord = {
  importId: string;
  status: string;
  headers: string[];
  mappingDraft: Record<string, string | null>;
  parserVersion: string;
  consentId: string;
  failureCode: string | null;
  createdAt: string;
  latestPlaylistVersion?: PlaylistVersion;
};

export type Mapping = {
  title: string;
  artist: string;
  album?: string;
  durationMs?: string;
  platformTrackId?: string;
  genre?: string;
  mood?: string;
  bpm?: string;
  tags?: string;
  audioEnergy?: string;
  audioBrightness?: string;
  audioDynamics?: string;
  audioPulse?: string;
  tempoConfidence?: string;
  audioFeatureVersion?: string;
};

export type PlaylistTrackPreview = {
  trackRef: string;
  row: number;
  title: string;
  artist: string;
  allowedSignals: Record<string, unknown>;
  excluded: boolean;
  duplicateOf: string | null;
};

export type PlaylistVersion = {
  playlistVersion: string;
  versionNumber: number;
  normalizedCount: number;
  invalidCount: number;
  duplicateCount: number;
  schemaVersion: string;
  lowSampleTrial: boolean;
  preview: PlaylistTrackPreview[];
  issues?: Array<Record<string, unknown>>;
};

export type TaskRecord = {
  taskId: string;
  runId: string;
  taskType: string;
  status: string;
  node: string;
  progress: number;
  attempt: number;
  errorCode: string | null;
  safeMessage: string | null;
  retryAllowed: boolean;
  output: Record<string, unknown>;
  updatedAt: string;
};

export type MoodCatalogItem = {
  moodId: string;
  displayName: string;
  definition: string;
  boundary: string;
};

export type ProfileTrack = {
  trackRef: string;
  row: number;
  title: string;
  artist: string;
  autoMoodId: string;
  finalMoodId: string;
  confidenceBand: string;
  reasonCode: string;
  sourceMode: string;
  sourceRefs: string[];
  evidence: string[];
  excluded: boolean;
  correctionSource: string;
};

export type PlaylistDna = {
  genres: string[];
  energy: number;
  keywords: string[];
  summary: string;
  representativeTracks: Array<{ title: string; artist: string }>;
  explanation: string;
};

export type MoodProfile = {
  revision: string;
  playlistVersion: string;
  parentRevision: string | null;
  revisionNumber: number;
  taxonomyVersion: string;
  modelVersion: string;
  promptVersion: string;
  ruleVersion: string;
  counts: Record<string, number>;
  percentages: Array<{ moodId: string; percent: number }>;
  knownCount: number;
  unknownCount: number;
  excludedCount: number;
  normalizedTotal: number;
  unknownRate: number;
  qualityFlags: string[];
  confirmedAt: string | null;
  createdAt: string;
  tracks: ProfileTrack[];
  page: number;
  pageSize: number;
  dna?: PlaylistDna;
};

export type Correction = {
  action: 'set_mood' | 'exclude' | 'include' | 'restore_auto';
  moodId?: string;
};

export type PetCandidate = {
  candidateId: string;
  candidateRecordId: string;
  candidateIndex: number;
  petSpec: {
    speciesId: string;
    bodyPaletteId: string;
    accessoryIds: string[];
    idleMotionId: string;
    traitBindings: Array<{ traitId: string; derivedFrom: string[] }>;
    mappingRuleVersion: string;
    assetCatalogVersion: string;
    developmentOnly: boolean;
    customized?: string[];
  };
  copy: {
    petName: string;
    intro: string;
    clickLines: string[];
    warnings: string[];
    promptVersion: string;
  };
  previewUrl: string;
};

export type PetSelection = {
  selectionId: string;
  taskId: string;
  candidateRecordId: string;
  selectionVersion: number;
  selectedAt: string;
};

export type PetActionItem = {
  action: string;
  previewUrl: string;
};

export type PetActionsResponse = {
  generationState: 'generating' | 'ready' | 'failed';
  fallbackMode: boolean;
  actions: PetActionItem[];
};

export type PetCustomization = {
  speciesId?: string;
  paletteId?: string;
  motionId?: string;
  headphone?: boolean;
};

export type PetAssetCatalog = {
  version: string;
  species: Array<{ id: string; name: string }>;
  palettes: Array<{ id: string; name: string; colors: string[] }>;
  motions: Array<{ id: string; name: string }>;
  headphone: { id: string; name: string };
};

export type PetCandidatesResponse = {
  task: TaskRecord;
  selection: PetSelection | null;
  candidates: PetCandidate[];
};

export type ConsentRecord = {
  consentId: string;
  purpose: string;
  fields: string[];
  provider: string;
  region: string;
  version: string;
  decision: 'allow' | 'deny';
  createdAt: string;
  revokedAt: string | null;
};

export type DeletionRecord = {
  deletionId: string;
  targetType: string;
  status: string;
  errorCode: string | null;
  requestedAt: string;
  completedAt: string | null;
  task: TaskRecord | null;
};

export type PersistedFlow = {
  tab?: AppTab;
  importId?: string;
  consentId?: string;
  playlistVersion?: string;
  analysisTaskId?: string;
  profileRevision?: string;
  petTaskId?: string;
  deletionId?: string;
};
