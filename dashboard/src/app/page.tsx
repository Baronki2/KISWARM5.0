'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardAction } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Activity,
  AlertTriangle,
  AlertCircle,
  Anchor,
  ArrowRight,
  Binary,
  Bot,
  Brain,
  Bug,
  Building2,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Cpu,
  Database,
  Download,
  Eye,
  FileCode,
  FlaskConical,
  Gauge,
  GitBranch,
  Globe,
  GitGraph,
  HardDrive,
  Heart,
  History,
  Icon,
  Info,
  Layers,
  LayoutDashboard,
  LineChart,
  Lock,
  LucideIcon,
  MemoryStick,
  Network,
  NetworkIcon,
  Octagon,
  Package,
  Pause,
  Play,
  Plug,
  Plus,
  QrCode,
  Radio,
  RefreshCw,
  RotateCcw,
  Scan,
  Server,
  Settings,
  Share2,
  Shield,
  ShieldAlert,
  Signal,
  Siren,
  Skull,
  Sparkles,
  SquareStack,
  Stars,
  Stethoscope,
  StopCircle,
  Sun,
  Sync,
  Terminal,
  ThumbsUp,
  Timer,
  ToggleLeft,
  TreeDeciduous,
  TrendingUp,
  TriangleAlert,
  Unlock,
  Upload,
  User,
  Users,
  Warning,
  Wifi,
  Wrench,
  Zap,
  ZoomIn,
} from 'lucide-react'

// ============================================================================
// Types
// ============================================================================

type ModuleStatus = 'active' | 'inactive' | 'error' | 'loading' | 'warning'
type InstallPhase = 'INIT' | 'SCANNING' | 'PLANNING' | 'INSTALLING' | 'VERIFYING' | 'DONE' | 'FAILED'
type InstallStepStatus = 'pending' | 'running' | 'success' | 'failed'
type LogLevel = 'INFO' | 'WARN' | 'ERROR' | 'DEBUG'

interface Module {
  id: string
  name: string
  description: string
  version: string
  category: string
  status: ModuleStatus
  icon: LucideIcon
}

interface InstallStep {
  id: string
  name: string
  status: InstallStepStatus
  message?: string
  progress?: number
}

interface LogEntry {
  id: string
  timestamp: Date
  level: LogLevel
  module: string
  message: string
}

interface APIEndpoint {
  name: string
  url: string
  status: 'online' | 'offline' | 'degraded'
  responseTime: number
  errorRate: number
  requests: number
}

interface SCADATag {
  id: string
  name: string
  value: number | string
  unit: string
  quality: 'good' | 'bad' | 'uncertain'
  timestamp: Date
  anomaly: boolean
}

interface KnowledgeNode {
  type: string
  count: number
}

interface Constraint {
  id: string
  name: string
  type: 'hard' | 'soft'
  description: string
  violations: number
  active: boolean
}

interface RLTrainingMetric {
  episode: number
  meanReward: number
  lagrangeMultiplier: number
  shieldRate: number
}

// ============================================================================
// Mock Data
// ============================================================================

const mockModules: Module[] = [
  // Core AKE (v2.1)
  { id: 'sentinel-bridge', name: 'Sentinel Bridge', description: 'Main API gateway and orchestrator', version: '2.1.0', category: 'Core AKE', status: 'active', icon: BridgeIcon },
  { id: 'swarm-debate', name: 'Swarm Debate Engine', description: 'Multi-agent consensus system', version: '2.1.0', category: 'Core AKE', status: 'active', icon: Brain },
  { id: 'vector-memory', name: 'Vector Memory (Qdrant)', description: 'Vector database integration', version: '2.1.0', category: 'Core AKE', status: 'active', icon: Database },
  
  // Intelligence Modules (v2.2)
  { id: 'semantic-conflict', name: 'Semantic Conflict Detector', description: 'Detects knowledge conflicts', version: '2.2.0', category: 'Intelligence', status: 'active', icon: GitBranch },
  { id: 'knowledge-decay', name: 'Knowledge Decay Engine', description: 'Manages knowledge freshness', version: '2.2.0', category: 'Intelligence', status: 'active', icon: TrendingUp },
  { id: 'model-tracker', name: 'Model Performance Tracker', description: 'Tracks model metrics', version: '2.2.0', category: 'Intelligence', status: 'active', icon: LineChart },
  { id: 'crypto-ledger', name: 'Cryptographic Ledger', description: 'Secure audit trail', version: '2.2.0', category: 'Intelligence', status: 'active', icon: Lock },
  { id: 'retrieval-guard', name: 'Retrieval Guard', description: 'RAG safety layer', version: '2.2.0', category: 'Intelligence', status: 'active', icon: Shield },
  { id: 'prompt-firewall', name: 'Adversarial Prompt Firewall', description: 'Input sanitization', version: '2.2.0', category: 'Intelligence', status: 'warning', icon: FirewallIcon },
  
  // Industrial AI (v3.0)
  { id: 'fuzzy-tuner', name: 'Fuzzy Auto-Tuner', description: 'Automatic parameter tuning', version: '3.0.0', category: 'Industrial AI', status: 'active', icon: Settings },
  { id: 'constrained-rl', name: 'Constrained RL Agent', description: 'Safe reinforcement learning', version: '3.0.0', category: 'Industrial AI', status: 'active', icon: Bot },
  { id: 'digital-twin', name: 'Digital Twin', description: 'Virtual system replica', version: '3.0.0', category: 'Industrial AI', status: 'active', icon: CopyIcon },
  { id: 'federated-mesh', name: 'Federated Mesh', description: 'Distributed learning mesh', version: '3.0.0', category: 'Industrial AI', status: 'active', icon: Network },
  
  // CIEC Core (v4.0)
  { id: 'plc-parser', name: 'PLC Semantic Parser', description: 'Module 11: PLC code analysis', version: '4.0.0', category: 'CIEC Core', status: 'active', icon: FileCode },
  { id: 'scada-observer', name: 'SCADA/OPC Observer', description: 'Module 12: SCADA monitoring', version: '4.0.0', category: 'CIEC Core', status: 'active', icon: Eye },
  { id: 'physics-twin', name: 'Digital Twin Physics', description: 'Module 13: Physics simulation', version: '4.0.0', category: 'CIEC Core', status: 'active', icon: AtomIcon },
  { id: 'rule-engine', name: 'Rule Constraint Engine', description: 'Module 14: Rule evaluation', version: '4.0.0', category: 'CIEC Core', status: 'active', icon: Gauge },
  { id: 'knowledge-graph', name: 'Knowledge Graph', description: 'Module 15: Graph database', version: '4.0.0', category: 'CIEC Core', status: 'active', icon: GitGraph },
  { id: 'actor-critic', name: 'Industrial Actor-Critic RL', description: 'Module 16: Advanced RL', version: '4.0.0', category: 'CIEC Core', status: 'active', icon: Zap },
  
  // v4.1+ Extensions
  { id: 'td3-controller', name: 'TD3 Industrial Controller', description: 'Twin-delayed DDPG', version: '4.1.0', category: 'v4.1+ Extensions', status: 'active', icon: Cpu },
  { id: 'ast-parser', name: 'IEC 61131-3 AST Parser', description: 'PLC syntax analysis', version: '4.1.0', category: 'v4.1+ Extensions', status: 'active', icon: Binary },
  { id: 'extended-physics', name: 'Extended Physics Twin', description: 'Advanced simulation', version: '4.1.0', category: 'v4.1+ Extensions', status: 'inactive', icon: FlaskConical },
  { id: 'vmware-orchestrator', name: 'VMware Orchestrator', description: 'VM management', version: '4.1.0', category: 'v4.1+ Extensions', status: 'active', icon: Server },
  { id: 'formal-verification', name: 'Formal Verification', description: 'Mathematical proofs', version: '4.1.0', category: 'v4.1+ Extensions', status: 'active', icon: CheckCircle2 },
  { id: 'byzantine-aggregator', name: 'Byzantine Aggregator', description: 'Fault-tolerant consensus', version: '4.1.0', category: 'v4.1+ Extensions', status: 'active', icon: Users },
  { id: 'mutation-governance', name: 'Mutation Governance', description: 'Evolution control', version: '4.1.0', category: 'v4.1+ Extensions', status: 'active', icon: Bug },
  
  // v4.2+ Features
  { id: 'explainability', name: 'Explainability Engine (XAI)', description: 'Decision explanation', version: '4.2.0', category: 'v4.2+ Features', status: 'active', icon: Sparkles },
  { id: 'predictive-maint', name: 'Predictive Maintenance', description: 'Failure prediction', version: '4.2.0', category: 'v4.2+ Features', status: 'active', icon: Wrench },
  { id: 'multiagent-coord', name: 'Multi-Agent Coordinator', description: 'Agent orchestration', version: '4.2.0', category: 'v4.2+ Features', status: 'active', icon: Users },
  { id: 'sil-verification', name: 'SIL Verification', description: 'Safety integrity level', version: '4.2.0', category: 'v4.2+ Features', status: 'active', icon: ShieldAlert },
  { id: 'digital-thread', name: 'Digital Thread Tracker', description: 'Lifecycle tracking', version: '4.2.0', category: 'v4.2+ Features', status: 'active', icon: History },
  
  // v4.3+ Security
  { id: 'ics-security', name: 'ICS Cybersecurity Engine', description: 'Industrial security', version: '4.3.0', category: 'v4.3+ Security', status: 'active', icon: Lock },
  { id: 'ot-monitor', name: 'OT Network Monitor', description: 'Network surveillance', version: '4.3.0', category: 'v4.3+ Security', status: 'active', icon: Wifi },
  
  // v4.6+ Automation
  { id: 'installer-agent', name: 'Installer Agent', description: 'Automated installation', version: '4.6.0', category: 'v4.6+ Automation', status: 'active', icon: Download },
  { id: 'system-scout', name: 'System Scout', description: 'Environment analysis', version: '4.6.0', category: 'v4.6+ Automation', status: 'active', icon: Scan },
  { id: 'repo-intel', name: 'Repo Intelligence', description: 'Code repository analysis', version: '4.6.0', category: 'v4.6+ Automation', status: 'active', icon: GithubIcon },
  { id: 'swarm-auditor', name: 'Swarm Auditor', description: 'Performance auditing', version: '4.6.0', category: 'v4.6+ Automation', status: 'active', icon: Stethoscope },
  
  // v4.7+ Evolution
  { id: 'experience-collector', name: 'Experience Collector', description: 'Learning data gathering', version: '4.7.0', category: 'v4.7+ Evolution', status: 'active', icon: Database },
  { id: 'feedback-channel', name: 'Feedback Channel', description: 'User feedback loop', version: '4.7.0', category: 'v4.7+ Evolution', status: 'active', icon: ThumbsUp },
  { id: 'sysadmin-agent', name: 'SysAdmin Agent', description: 'System administration', version: '4.7.0', category: 'v4.7+ Evolution', status: 'active', icon: User },
  
  // v4.9+ Resilience
  { id: 'software-ark', name: 'Software Ark', description: 'Backup and recovery', version: '4.9.0', category: 'v4.9+ Resilience', status: 'active', icon: Anchor },
  { id: 'ark-manager', name: 'Ark Manager', description: 'Ark coordination', version: '4.9.0', category: 'v4.9+ Resilience', status: 'active', icon: Package },
  { id: 'bootstrap-engine', name: 'Bootstrap Engine', description: 'System initialization', version: '4.9.0', category: 'v4.9+ Resilience', status: 'active', icon: Play },
  
  // v5.1 Solar Chase (Planetary Machine)
  { id: 'solar-chase-coordinator', name: 'SolarChaseCoordinator', description: 'Module 34: Sun-following compute orchestrator', version: '5.1.0', category: 'v5.1 Solar Chase', status: 'active', icon: Sun },
  { id: 'energy-pivot-engine', name: 'EnergyOvercapacityPivotEngine', description: 'Module 35: Zero Feed-In enforcement', version: '5.1.0', category: 'v5.1 Solar Chase', status: 'active', icon: Zap },
  { id: 'planetary-sun-follower', name: 'PlanetarySunFollowerMesh', description: 'Module 36: Global compute handoff mesh', version: '5.1.0', category: 'v5.1 Solar Chase', status: 'active', icon: Globe },
  { id: 'zero-emission-tracker', name: 'ZeroEmissionComputeTracker', description: 'Module 37: Immutable ESG ledger', version: '5.1.0', category: 'v5.1 Solar Chase', status: 'active', icon: TreeDeciduous },
  { id: 'sun-handoff-validator', name: 'SunHandoffValidator', description: 'Module 38: Migration safety guard', version: '5.1.0', category: 'v5.1 Solar Chase', status: 'active', icon: Shield },
]

const mockInstallSteps: InstallStep[] = [
  { id: '1', name: 'Environment Check', status: 'success', message: 'All dependencies verified', progress: 100 },
  { id: '2', name: 'Python 3.11+ Setup', status: 'success', message: 'Python 3.11.4 installed', progress: 100 },
  { id: '3', name: 'Ollama Installation', status: 'success', message: 'Ollama running on port 11434', progress: 100 },
  { id: '4', name: 'Qdrant Vector DB', status: 'success', message: 'Vector database initialized', progress: 100 },
  { id: '5', name: 'Sentinel API Setup', status: 'running', message: 'Configuring API endpoints...', progress: 67 },
  { id: '6', name: 'Module Registration', status: 'pending', message: 'Waiting for API setup', progress: 0 },
  { id: '7', name: 'Knowledge Graph Init', status: 'pending', message: 'Waiting for modules', progress: 0 },
  { id: '8', name: 'Verification Tests', status: 'pending', message: 'Waiting for initialization', progress: 0 },
]

const mockLogs: LogEntry[] = [
  { id: '1', timestamp: new Date(Date.now() - 5000), level: 'INFO', module: 'Sentinel Bridge', message: 'API server started on port 11436' },
  { id: '2', timestamp: new Date(Date.now() - 4500), level: 'INFO', module: 'Vector Memory', message: 'Connected to Qdrant at localhost:6333' },
  { id: '3', timestamp: new Date(Date.now() - 4000), level: 'INFO', module: 'Swarm Debate', message: 'Initialized 3 debate agents' },
  { id: '4', timestamp: new Date(Date.now() - 3500), level: 'WARN', module: 'Prompt Firewall', message: 'High traffic detected, enabling rate limiting' },
  { id: '5', timestamp: new Date(Date.now() - 3000), level: 'INFO', module: 'PLC Parser', message: 'Parsed 156 ladder logic rungs' },
  { id: '6', timestamp: new Date(Date.now() - 2500), level: 'DEBUG', module: 'SCADA Observer', message: 'Subscribed to 48 OPC tags' },
  { id: '7', timestamp: new Date(Date.now() - 2000), level: 'INFO', module: 'Knowledge Graph', message: 'Added 234 nodes, 567 edges' },
  { id: '8', timestamp: new Date(Date.now() - 1500), level: 'ERROR', module: 'Federated Mesh', message: 'Connection timeout to peer node-3' },
  { id: '9', timestamp: new Date(Date.now() - 1000), level: 'INFO', module: 'Installer Agent', message: 'Phase transition: SCANNING → PLANNING' },
  { id: '10', timestamp: new Date(Date.now() - 500), level: 'INFO', module: 'Digital Twin', message: 'Physics simulation step completed (t=1.234s)' },
]

const mockAPIEndpoints: APIEndpoint[] = [
  { name: 'Sentinel API', url: 'localhost:11436', status: 'online', responseTime: 12, errorRate: 0.01, requests: 15847 },
  { name: 'Ollama LLM', url: 'localhost:11434', status: 'online', responseTime: 234, errorRate: 0.02, requests: 3421 },
  { name: 'Qdrant Vector DB', url: 'localhost:6333', status: 'online', responseTime: 8, errorRate: 0.0, requests: 8923 },
  { name: 'Prometheus', url: 'localhost:9090', status: 'degraded', responseTime: 156, errorRate: 0.05, requests: 4521 },
]

const mockSCADATags: SCADATag[] = [
  { id: '1', name: 'Reactor_Temp_01', value: 342.5, unit: '°C', quality: 'good', timestamp: new Date(), anomaly: false },
  { id: '2', name: 'Pressure_Vessel_A', value: 15.7, unit: 'bar', quality: 'good', timestamp: new Date(), anomaly: false },
  { id: '3', name: 'Flow_Rate_Main', value: 1245, unit: 'L/min', quality: 'good', timestamp: new Date(), anomaly: false },
  { id: '4', name: 'Motor_Speed_P01', value: 1485, unit: 'RPM', quality: 'good', timestamp: new Date(), anomaly: false },
  { id: '5', name: 'Valve_Position_V12', value: 78.5, unit: '%', quality: 'uncertain', timestamp: new Date(), anomaly: true },
  { id: '6', name: 'Level_Tank_B2', value: 67.2, unit: '%', quality: 'good', timestamp: new Date(), anomaly: false },
  { id: '7', name: 'pH_Sensor_A1', value: 7.42, unit: 'pH', quality: 'good', timestamp: new Date(), anomaly: false },
  { id: '8', name: 'Conductivity_C01', value: 2.34, unit: 'mS/cm', quality: 'bad', timestamp: new Date(), anomaly: true },
]

const mockKnowledgeNodes: KnowledgeNode[] = [
  { type: 'Entity', count: 12847 },
  { type: 'Process', count: 3421 },
  { type: 'Equipment', count: 892 },
  { type: 'Constraint', count: 456 },
  { type: 'Rule', count: 1234 },
  { type: 'Sensor', count: 567 },
]

const mockConstraints: Constraint[] = [
  { id: '1', name: 'MAX_TEMP_LIMIT', type: 'hard', description: 'Maximum temperature limit (400°C)', violations: 0, active: true },
  { id: '2', name: 'MIN_FLOW_RATE', type: 'hard', description: 'Minimum flow rate (500 L/min)', violations: 2, active: true },
  { id: '3', name: 'PRESSURE_RANGE', type: 'hard', description: 'Operating pressure range (10-20 bar)', violations: 1, active: true },
  { id: '4', name: 'EFFICIENCY_TARGET', type: 'soft', description: 'Target efficiency > 85%', violations: 5, active: true },
  { id: '5', name: 'RESPONSE_TIME', type: 'soft', description: 'Response time < 100ms', violations: 12, active: true },
  { id: '6', name: 'ENERGY_CONSUMPTION', type: 'soft', description: 'Energy consumption optimization', violations: 3, active: false },
]

const mockRLMetrics: RLTrainingMetric[] = Array.from({ length: 20 }, (_, i) => ({
  episode: i + 1,
  meanReward: -100 + i * 8 + Math.random() * 10,
  lagrangeMultiplier: 0.5 + Math.random() * 0.3,
  shieldRate: 0.1 - i * 0.003 + Math.random() * 0.02,
}))

// Custom icons (not in lucide-react)
function BridgeIcon(props: React.ComponentProps<typeof Icon>) {
  return <NetworkIcon {...props} />
}

function CopyIcon(props: React.ComponentProps<typeof Icon>) {
  return <SquareStack {...props} />
}

function AtomIcon(props: React.ComponentProps<typeof Icon>) {
  return <CircleDot {...props} />
}

function FirewallIcon(props: React.ComponentProps<typeof Icon>) {
  return <Octagon {...props} />
}

function GithubIcon(props: React.ComponentProps<typeof Icon>) {
  return <GitBranch {...props} />
}

// ============================================================================
// Helper Functions
// ============================================================================

function getStatusColor(status: ModuleStatus): string {
  switch (status) {
    case 'active': return 'bg-emerald-500'
    case 'inactive': return 'bg-gray-500'
    case 'error': return 'bg-red-500'
    case 'loading': return 'bg-amber-500'
    case 'warning': return 'bg-yellow-500'
    default: return 'bg-gray-500'
  }
}

function getStatusBadgeVariant(status: ModuleStatus): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'active': return 'default'
    case 'inactive': return 'secondary'
    case 'error': return 'destructive'
    case 'loading': return 'outline'
    case 'warning': return 'outline'
    default: return 'secondary'
  }
}

function getLogColor(level: LogLevel): string {
  switch (level) {
    case 'INFO': return 'text-blue-400'
    case 'WARN': return 'text-yellow-400'
    case 'ERROR': return 'text-red-400'
    case 'DEBUG': return 'text-gray-400'
    default: return 'text-gray-400'
  }
}

function getQualityColor(quality: 'good' | 'bad' | 'uncertain'): string {
  switch (quality) {
    case 'good': return 'text-emerald-400'
    case 'bad': return 'text-red-400'
    case 'uncertain': return 'text-yellow-400'
    default: return 'text-gray-400'
  }
}

function formatUptime(startTime: Date): string {
  const diff = Date.now() - startTime.getTime()
  const hours = Math.floor(diff / (1000 * 60 * 60))
  const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))
  const seconds = Math.floor((diff % (1000 * 60)) / 1000)
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}

// ============================================================================
// Components
// ============================================================================

// Circular Progress Component
function CircularProgress({ value, size = 120, strokeWidth = 8, children }: { 
  value: number
  size?: number
  strokeWidth?: number
  children?: React.ReactNode 
}) {
  const radius = (size - strokeWidth) / 2
  const circumference = radius * 2 * Math.PI
  const offset = circumference - (value / 100) * circumference

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="transform -rotate-90">
        <circle
          className="text-gray-700"
          strokeWidth={strokeWidth}
          stroke="currentColor"
          fill="transparent"
          r={radius}
          cx={size / 2}
          cy={size / 2}
        />
        <circle
          className="text-emerald-500 transition-all duration-500"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          stroke="currentColor"
          fill="transparent"
          r={radius}
          cx={size / 2}
          cy={size / 2}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        {children}
      </div>
    </div>
  )
}

// Status Dot Component
function StatusDot({ status, pulse = false }: { status: 'online' | 'offline' | 'degraded', pulse?: boolean }) {
  const color = status === 'online' ? 'bg-emerald-500' : status === 'offline' ? 'bg-red-500' : 'bg-yellow-500'
  
  return (
    <span className={`relative flex h-3 w-3`}>
      {pulse && status === 'online' && (
        <span className={`absolute inline-flex h-full w-full rounded-full ${color} opacity-75 animate-ping`}></span>
      )}
      <span className={`relative inline-flex rounded-full h-3 w-3 ${color}`}></span>
    </span>
  )
}

// Module Card Component
function ModuleCard({ module }: { module: Module }) {
  const Icon = module.icon
  
  return (
    <Card className="bg-gray-800/50 border-gray-700 hover:border-gray-600 transition-all duration-200 hover:shadow-lg hover:shadow-gray-900/50">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${module.status === 'active' ? 'bg-emerald-500/20 text-emerald-400' : module.status === 'error' ? 'bg-red-500/20 text-red-400' : module.status === 'warning' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-gray-500/20 text-gray-400'}`}>
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <h4 className="text-sm font-medium text-white">{module.name}</h4>
              <p className="text-xs text-gray-500">v{module.version}</p>
            </div>
          </div>
          <div className={`w-2 h-2 rounded-full ${getStatusColor(module.status)} ${module.status === 'active' ? 'animate-pulse' : ''}`} />
        </div>
        <p className="text-xs text-gray-400 mt-2 line-clamp-1">{module.description}</p>
      </CardContent>
    </Card>
  )
}

// Install Step Card Component
function InstallStepCard({ step, onRetry }: { step: InstallStep, onRetry?: () => void }) {
  return (
    <div className={`p-3 rounded-lg border transition-all duration-200 ${
      step.status === 'success' ? 'bg-emerald-500/10 border-emerald-500/30' :
      step.status === 'running' ? 'bg-blue-500/10 border-blue-500/30' :
      step.status === 'failed' ? 'bg-red-500/10 border-red-500/30' :
      'bg-gray-800/50 border-gray-700'
    }`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {step.status === 'success' && <CheckCircle2 className="h-5 w-5 text-emerald-400" />}
          {step.status === 'running' && <RefreshCw className="h-5 w-5 text-blue-400 animate-spin" />}
          {step.status === 'failed' && <AlertCircle className="h-5 w-5 text-red-400" />}
          {step.status === 'pending' && <CircleDot className="h-5 w-5 text-gray-500" />}
          <div>
            <h4 className="text-sm font-medium text-white">{step.name}</h4>
            <p className="text-xs text-gray-400">{step.message}</p>
          </div>
        </div>
        {step.status === 'failed' && (
          <Button size="sm" variant="outline" onClick={onRetry} className="h-7 text-xs">
            <RotateCcw className="h-3 w-3 mr-1" />
            Retry
          </Button>
        )}
      </div>
      {step.status === 'running' && (
        <Progress value={step.progress} className="h-1 mt-2" />
      )}
    </div>
  )
}

// Log Entry Component
function LogEntryRow({ log }: { log: LogEntry }) {
  return (
    <div className="flex items-start gap-3 py-1.5 px-2 hover:bg-gray-800/50 rounded text-xs font-mono">
      <span className="text-gray-500 w-20 shrink-0">
        {log.timestamp.toLocaleTimeString()}
      </span>
      <span className={`w-12 shrink-0 font-semibold ${getLogColor(log.level)}`}>
        [{log.level}]
      </span>
      <span className="text-purple-400 w-32 shrink-0 truncate">
        {log.module}
      </span>
      <span className="text-gray-300 flex-1">
        {log.message}
      </span>
    </div>
  )
}

// API Endpoint Card Component
function APIEndpointCard({ endpoint }: { endpoint: APIEndpoint }) {
  return (
    <div className="flex items-center justify-between p-3 bg-gray-800/50 rounded-lg border border-gray-700">
      <div className="flex items-center gap-3">
        <StatusDot status={endpoint.status} pulse={endpoint.status === 'online'} />
        <div>
          <h4 className="text-sm font-medium text-white">{endpoint.name}</h4>
          <p className="text-xs text-gray-500">{endpoint.url}</p>
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs">
        <div className="text-right">
          <p className="text-gray-400">Response</p>
          <p className={`font-mono ${endpoint.responseTime < 50 ? 'text-emerald-400' : endpoint.responseTime < 200 ? 'text-yellow-400' : 'text-red-400'}`}>
            {endpoint.responseTime}ms
          </p>
        </div>
        <div className="text-right">
          <p className="text-gray-400">Error Rate</p>
          <p className={`font-mono ${endpoint.errorRate < 0.01 ? 'text-emerald-400' : endpoint.errorRate < 0.05 ? 'text-yellow-400' : 'text-red-400'}`}>
            {(endpoint.errorRate * 100).toFixed(1)}%
          </p>
        </div>
        <div className="text-right">
          <p className="text-gray-400">Requests</p>
          <p className="font-mono text-blue-400">{endpoint.requests.toLocaleString()}</p>
        </div>
      </div>
    </div>
  )
}

// SCADA Tag Row Component
function SCADATagRow({ tag }: { tag: SCADATag }) {
  return (
    <div className={`flex items-center justify-between p-2 rounded-lg ${tag.anomaly ? 'bg-red-500/10 border border-red-500/30' : 'bg-gray-800/30'}`}>
      <div className="flex items-center gap-3">
        {tag.anomaly && <AlertTriangle className="h-4 w-4 text-red-400" />}
        <div>
          <h4 className="text-sm font-medium text-white">{tag.name}</h4>
          <p className="text-xs text-gray-500">{tag.timestamp.toLocaleTimeString()}</p>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <span className={`text-lg font-mono ${getQualityColor(tag.quality)}`}>
          {typeof tag.value === 'number' ? tag.value.toFixed(2) : tag.value}
          <span className="text-xs text-gray-500 ml-1">{tag.unit}</span>
        </span>
        <Badge variant={tag.quality === 'good' ? 'default' : tag.quality === 'bad' ? 'destructive' : 'secondary'} className="text-xs">
          {tag.quality}
        </Badge>
      </div>
    </div>
  )
}

// Sparkline Chart Component (simple)
function Sparkline({ data, width = 200, height = 40 }: { data: number[], width?: number, height?: number }) {
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  
  const points = data.map((value, index) => {
    const x = (index / (data.length - 1)) * width
    const y = height - ((value - min) / range) * height
    return `${x},${y}`
  }).join(' ')

  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
        className="text-emerald-400"
      />
    </svg>
  )
}

// ============================================================================
// Main Dashboard Component
// ============================================================================

export default function KISWARMDashboard() {
  const [startTime] = useState(new Date(Date.now() - 3600000 * 24)) // 24 hours ago
  const [uptime, setUptime] = useState('00:00:00')
  const [modules] = useState<Module[]>(mockModules)
  const [installPhase, setInstallPhase] = useState<InstallPhase>('INSTALLING')
  const [installSteps, setInstallSteps] = useState<InstallStep[]>(mockInstallSteps)
  const [logs, setLogs] = useState<LogEntry[]>(mockLogs)
  const [apiEndpoints] = useState<APIEndpoint[]>(mockAPIEndpoints)
  const [scadaTags] = useState<SCADATag[]>(mockSCADATags)
  const [knowledgeNodes] = useState<KnowledgeNode[]>(mockKnowledgeNodes)
  const [constraints] = useState<Constraint[]>(mockConstraints)
  const [rlMetrics] = useState<RLTrainingMetric[]>(mockRLMetrics)
  const [healthScore, setHealthScore] = useState(94)
  const [logFilter, setLogFilter] = useState<LogLevel | 'ALL'>('ALL')
  const [logSearch, setLogSearch] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [systemResources, setSystemResources] = useState({ cpu: 45, memory: 62, disk: 38 })
  const logContainerRef = useRef<HTMLDivElement>(null)

  // Update uptime
  useEffect(() => {
    const interval = setInterval(() => {
      setUptime(formatUptime(startTime))
    }, 1000)
    return () => clearInterval(interval)
  }, [startTime])

  // Fetch real data from KISWARM API
  useEffect(() => {
    const fetchRealData = async () => {
      try {
        // Try to fetch from real API via proxy
        const response = await fetch('/api/kiswarm?endpoint=health')
        if (response.ok) {
          const data = await response.json()
          if (!data.mock && data.status === 'active') {
            // Real API data - update health score based on stats
            const stats = data.stats || {}
            const totalRequests = Object.values(stats).reduce((a: number, b) => a + (typeof b === 'number' ? b : 0), 0)
            const errorRate = stats.firewall_blocked && stats.firewall_scans 
              ? stats.firewall_blocked / stats.firewall_scans 
              : 0
            setHealthScore(Math.max(80, Math.min(100, 100 - errorRate * 50)))
          }
        }
      } catch {
        // Use mock data on error
      }
    }
    
    fetchRealData()
    const interval = setInterval(fetchRealData, 5000)
    return () => clearInterval(interval)
  }, [])

  // Simulate real-time data updates
  useEffect(() => {
    const interval = setInterval(() => {
      // Update health score slightly
      setHealthScore(prev => Math.max(85, Math.min(100, prev + (Math.random() - 0.5) * 2)))
      
      // Update system resources
      setSystemResources(prev => ({
        cpu: Math.max(20, Math.min(90, prev.cpu + (Math.random() - 0.5) * 10)),
        memory: Math.max(40, Math.min(85, prev.memory + (Math.random() - 0.5) * 5)),
        disk: Math.max(30, Math.min(70, prev.disk + (Math.random() - 0.5) * 2)),
      }))
      
      // Add new log occasionally
      if (Math.random() > 0.7) {
        const newLog: LogEntry = {
          id: Date.now().toString(),
          timestamp: new Date(),
          level: ['INFO', 'WARN', 'DEBUG', 'ERROR'][Math.floor(Math.random() * 4)] as LogLevel,
          module: modules[Math.floor(Math.random() * modules.length)].name,
          message: [
            'Processing request completed',
            'Cache hit ratio: 94.2%',
            'Model inference: 234ms',
            'Vector search: 45 results',
            'Connection pool refreshed',
            'Health check passed',
          ][Math.floor(Math.random() * 6)],
        }
        setLogs(prev => [newLog, ...prev].slice(0, 100))
      }
    }, 3000)
    
    return () => clearInterval(interval)
  }, [modules])

  // Auto-scroll logs
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = 0
    }
  }, [logs, autoScroll])

  // Handle refresh
  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true)
    // Simulate API call
    await new Promise(resolve => setTimeout(resolve, 1000))
    setIsRefreshing(false)
  }, [])

  // Handle retry step
  const handleRetryStep = useCallback((stepId: string) => {
    setInstallSteps(prev => prev.map(step => 
      step.id === stepId ? { ...step, status: 'running' as InstallStepStatus, progress: 0 } : step
    ))
  }, [])

  // Filter logs
  const filteredLogs = logs.filter(log => {
    if (logFilter !== 'ALL' && log.level !== logFilter) return false
    if (logSearch && !log.message.toLowerCase().includes(logSearch.toLowerCase()) && 
        !log.module.toLowerCase().includes(logSearch.toLowerCase())) return false
    return true
  })

  // Group modules by category
  const modulesByCategory = modules.reduce((acc, module) => {
    if (!acc[module.category]) acc[module.category] = []
    acc[module.category].push(module)
    return acc
  }, {} as Record<string, Module[]>)

  // Calculate stats
  const activeModules = modules.filter(m => m.status === 'active').length
  const totalModules = modules.length
  const errorModules = modules.filter(m => m.status === 'error').length

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-gray-900/95 backdrop-blur border-b border-gray-800">
        <div className="px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center">
                <Zap className="h-5 w-5 text-white" />
              </div>
              <div>
                <h1 className="text-lg font-bold tracking-tight">KISWARM</h1>
                <p className="text-xs text-gray-500">v5.1 PLANETARY MACHINE</p>
              </div>
            </div>
          </div>
          
          <div className="flex items-center gap-6">
            {/* System Status */}
            <div className="flex items-center gap-4 px-4 py-1.5 bg-gray-800 rounded-lg">
              <div className="flex items-center gap-2">
                <StatusDot status="online" pulse />
                <span className="text-sm text-gray-300">System Online</span>
              </div>
              <div className="h-4 w-px bg-gray-700" />
              <div className="flex items-center gap-2">
                <Timer className="h-4 w-4 text-gray-400" />
                <span className="text-sm font-mono text-gray-300">{uptime}</span>
              </div>
            </div>
            
            {/* Actions */}
            <div className="flex items-center gap-2">
              <Button 
                variant="outline" 
                size="sm" 
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="border-gray-700 hover:bg-gray-800"
              >
                <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex">
        {/* Sidebar Navigation */}
        <aside className="sticky top-[57px] h-[calc(100vh-57px)] w-56 bg-gray-900/50 border-r border-gray-800 p-4">
          <nav className="space-y-1">
            {[
              { id: 'overview', label: 'Overview', icon: LayoutDashboard },
              { id: 'modules', label: 'Modules', icon: Layers },
              { id: 'installer', label: 'Installer', icon: Download },
              { id: 'api', label: 'API Health', icon: Activity },
              { id: 'logs', label: 'Logs', icon: Terminal },
              { id: 'scada', label: 'SCADA', icon: Radio },
              { id: 'knowledge', label: 'Knowledge', icon: GitGraph },
              { id: 'constraints', label: 'Constraints', icon: Gauge },
              { id: 'training', label: 'RL Training', icon: LineChart },
            ].map(item => (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all ${
                  activeTab === item.id 
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' 
                    : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                }`}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </button>
            ))}
          </nav>
          
          {/* Quick Stats */}
          <div className="mt-6 pt-6 border-t border-gray-800">
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Quick Stats</h3>
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-400">Active Modules</span>
                <span className="text-xs font-mono text-emerald-400">{activeModules}/{totalModules}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-400">Health Score</span>
                <span className="text-xs font-mono text-emerald-400">{healthScore.toFixed(0)}%</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-400">Errors</span>
                <span className={`text-xs font-mono ${errorModules > 0 ? 'text-red-400' : 'text-emerald-400'}`}>{errorModules}</span>
              </div>
            </div>
          </div>
        </aside>

        {/* Main Panel */}
        <main className="flex-1 p-6 overflow-auto h-[calc(100vh-57px)]">
          {/* Overview Tab */}
          {activeTab === 'overview' && (
            <div className="space-y-6">
              {/* Top Stats Row */}
              <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                {/* Health Score */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-6 flex items-center gap-6">
                    <CircularProgress value={healthScore} size={100} strokeWidth={8}>
                      <div className="text-center">
                        <span className="text-2xl font-bold text-white">{healthScore.toFixed(0)}</span>
                        <span className="text-xs text-gray-400 block">Score</span>
                      </div>
                    </CircularProgress>
                    <div>
                      <h3 className="text-sm font-medium text-gray-400">System Health</h3>
                      <p className="text-2xl font-bold text-white mt-1">
                        {healthScore >= 90 ? 'Excellent' : healthScore >= 75 ? 'Good' : healthScore >= 50 ? 'Fair' : 'Poor'}
                      </p>
                      <Badge variant="outline" className="mt-2 border-emerald-500/30 text-emerald-400">
                        <CheckCircle2 className="h-3 w-3 mr-1" />
                        All Systems Operational
                      </Badge>
                    </div>
                  </CardContent>
                </Card>

                {/* Active Modules */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-6">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-medium text-gray-400">Active Modules</h3>
                        <p className="text-3xl font-bold text-white mt-1">{activeModules}</p>
                        <p className="text-xs text-gray-500 mt-1">of {totalModules} total</p>
                      </div>
                      <div className="w-14 h-14 rounded-xl bg-blue-500/20 flex items-center justify-center">
                        <Layers className="h-7 w-7 text-blue-400" />
                      </div>
                    </div>
                    <Progress value={(activeModules / totalModules) * 100} className="h-2 mt-4" />
                  </CardContent>
                </Card>

                {/* API Status */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-6">
                    <h3 className="text-sm font-medium text-gray-400 mb-4">API Status</h3>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <StatusDot status="online" />
                          <span className="text-sm text-gray-300">Ollama</span>
                        </div>
                        <span className="text-xs font-mono text-gray-500">:11434</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <StatusDot status="online" />
                          <span className="text-sm text-gray-300">Sentinel API</span>
                        </div>
                        <span className="text-xs font-mono text-gray-500">:11436</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <StatusDot status="online" />
                          <span className="text-sm text-gray-300">Qdrant</span>
                        </div>
                        <span className="text-xs font-mono text-gray-500">:6333</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* Uptime */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-6">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-medium text-gray-400">System Uptime</h3>
                        <p className="text-3xl font-mono font-bold text-white mt-1">{uptime}</p>
                        <p className="text-xs text-emerald-400 mt-1 flex items-center gap-1">
                          <Heart className="h-3 w-3" />
                          Running smoothly
                        </p>
                      </div>
                      <div className="w-14 h-14 rounded-xl bg-emerald-500/20 flex items-center justify-center">
                        <Timer className="h-7 w-7 text-emerald-400" />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* Resource Usage */}
              <Card className="bg-gray-800/50 border-gray-700">
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">System Resources</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-3 gap-6">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-gray-400 flex items-center gap-2">
                          <Cpu className="h-4 w-4" />
                          CPU Usage
                        </span>
                        <span className="text-sm font-mono text-white">{systemResources.cpu.toFixed(0)}%</span>
                      </div>
                      <Progress value={systemResources.cpu} className="h-2" />
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-gray-400 flex items-center gap-2">
                          <MemoryStick className="h-4 w-4" />
                          Memory
                        </span>
                        <span className="text-sm font-mono text-white">{systemResources.memory.toFixed(0)}%</span>
                      </div>
                      <Progress value={systemResources.memory} className="h-2" />
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-gray-400 flex items-center gap-2">
                          <HardDrive className="h-4 w-4" />
                          Disk
                        </span>
                        <span className="text-sm font-mono text-white">{systemResources.disk.toFixed(0)}%</span>
                      </div>
                      <Progress value={systemResources.disk} className="h-2" />
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Module Categories Overview */}
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
                {Object.entries(modulesByCategory).slice(0, 6).map(([category, mods]) => (
                  <Card key={category} className="bg-gray-800/50 border-gray-700">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm flex items-center justify-between">
                        {category}
                        <Badge variant="outline" className="text-xs">
                          {mods.filter(m => m.status === 'active').length}/{mods.length}
                        </Badge>
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-1.5">
                        {mods.map(mod => (
                          <div 
                            key={mod.id}
                            className={`w-2 h-2 rounded-full ${getStatusColor(mod.status)}`}
                            title={mod.name}
                          />
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Modules Tab */}
          {activeTab === 'modules' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">Module Status</h2>
                  <p className="text-gray-400">KISWARM v4.9 - {totalModules} modules across all categories</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-500/30">
                    {activeModules} Active
                  </Badge>
                  {errorModules > 0 && (
                    <Badge className="bg-red-500/20 text-red-400 border-red-500/30">
                      {errorModules} Errors
                    </Badge>
                  )}
                </div>
              </div>
              
              {Object.entries(modulesByCategory).map(([category, mods]) => (
                <div key={category} className="space-y-3">
                  <div className="flex items-center gap-3">
                    <h3 className="text-lg font-semibold">{category}</h3>
                    <Badge variant="outline" className="text-xs">
                      {mods.filter(m => m.status === 'active').length}/{mods.length} active
                    </Badge>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                    {mods.map(module => (
                      <ModuleCard key={module.id} module={module} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Installer Tab */}
          {activeTab === 'installer' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">Installer Agent</h2>
                  <p className="text-gray-400">Automated system installation and configuration</p>
                </div>
                <Badge className={`${installPhase === 'DONE' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : installPhase === 'FAILED' ? 'bg-red-500/20 text-red-400 border-red-500/30' : 'bg-blue-500/20 text-blue-400 border-blue-500/30'}`}>
                  {installPhase}
                </Badge>
              </div>

              {/* Phase Progress */}
              <Card className="bg-gray-800/50 border-gray-700">
                <CardContent className="p-6">
                  <div className="flex items-center justify-between">
                    {(['INIT', 'SCANNING', 'PLANNING', 'INSTALLING', 'VERIFYING', 'DONE'] as InstallPhase[]).map((phase, index, arr) => (
                      <div key={phase} className="flex items-center">
                        <div className="flex flex-col items-center">
                          <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-medium ${
                            arr.indexOf(installPhase) > index ? 'bg-emerald-500 text-white' :
                            phase === installPhase ? 'bg-blue-500 text-white animate-pulse' :
                            'bg-gray-700 text-gray-400'
                          }`}>
                            {arr.indexOf(installPhase) > index ? <Check className="h-5 w-5" /> : index + 1}
                          </div>
                          <span className={`text-xs mt-2 ${
                            arr.indexOf(installPhase) >= index ? 'text-white' : 'text-gray-500'
                          }`}>{phase}</span>
                        </div>
                        {index < arr.length - 1 && (
                          <div className={`w-16 h-0.5 mx-2 ${
                            arr.indexOf(installPhase) > index ? 'bg-emerald-500' : 'bg-gray-700'
                          }`} />
                        )}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Installation Steps */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Installation Steps</CardTitle>
                    <CardDescription>Step-by-step installation progress</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {installSteps.map(step => (
                      <InstallStepCard 
                        key={step.id} 
                        step={step} 
                        onRetry={() => handleRetryStep(step.id)}
                      />
                    ))}
                  </CardContent>
                </Card>

                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Installation Log</CardTitle>
                    <CardDescription>Real-time installation output</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ScrollArea className="h-80 bg-gray-900 rounded-lg p-3">
                      <div className="space-y-1 font-mono text-xs">
                        <div className="text-gray-500">[{new Date().toISOString()}] Starting installation...</div>
                        <div className="text-emerald-400">[OK] Environment check passed</div>
                        <div className="text-emerald-400">[OK] Python 3.11.4 detected</div>
                        <div className="text-emerald-400">[OK] Ollama service running on port 11434</div>
                        <div className="text-emerald-400">[OK] Qdrant vector DB connected</div>
                        <div className="text-blue-400">[INFO] Configuring Sentinel API endpoints...</div>
                        <div className="text-blue-400">[INFO] Registering 45 modules...</div>
                        <div className="text-yellow-400">[WARN] High memory usage detected (72%)</div>
                        <div className="text-blue-400">[INFO] Initializing knowledge graph...</div>
                        <div className="text-gray-500">[{new Date().toISOString()}] Installation in progress...</div>
                      </div>
                    </ScrollArea>
                  </CardContent>
                </Card>
              </div>
            </div>
          )}

          {/* API Health Tab */}
          {activeTab === 'api' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">API Health Monitoring</h2>
                  <p className="text-gray-400">Real-time API endpoint status and metrics</p>
                </div>
                <Button variant="outline" onClick={handleRefresh} disabled={isRefreshing}>
                  <RefreshCw className={`h-4 w-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
                  Refresh
                </Button>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                        <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                      </div>
                      <div>
                        <p className="text-xs text-gray-400">Online</p>
                        <p className="text-xl font-bold">{apiEndpoints.filter(e => e.status === 'online').length}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-yellow-500/20 flex items-center justify-center">
                        <AlertTriangle className="h-5 w-5 text-yellow-400" />
                      </div>
                      <div>
                        <p className="text-xs text-gray-400">Degraded</p>
                        <p className="text-xl font-bold">{apiEndpoints.filter(e => e.status === 'degraded').length}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center">
                        <Activity className="h-5 w-5 text-blue-400" />
                      </div>
                      <div>
                        <p className="text-xs text-gray-400">Total Requests</p>
                        <p className="text-xl font-bold">{apiEndpoints.reduce((acc, e) => acc + e.requests, 0).toLocaleString()}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-purple-500/20 flex items-center justify-center">
                        <Gauge className="h-5 w-5 text-purple-400" />
                      </div>
                      <div>
                        <p className="text-xs text-gray-400">Avg Response</p>
                        <p className="text-xl font-bold">{(apiEndpoints.reduce((acc, e) => acc + e.responseTime, 0) / apiEndpoints.length).toFixed(0)}ms</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>

              <div className="space-y-3">
                {apiEndpoints.map(endpoint => (
                  <APIEndpointCard key={endpoint.name} endpoint={endpoint} />
                ))}
              </div>
            </div>
          )}

          {/* Logs Tab */}
          {activeTab === 'logs' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">Real-time Log Viewer</h2>
                  <p className="text-gray-400">System logs and event stream</p>
                </div>
              </div>

              {/* Log Controls */}
              <Card className="bg-gray-800/50 border-gray-700">
                <CardContent className="p-4">
                  <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                      <Label className="text-sm text-gray-400">Filter:</Label>
                      <div className="flex gap-1">
                        {(['ALL', 'INFO', 'WARN', 'ERROR', 'DEBUG'] as const).map(level => (
                          <Button
                            key={level}
                            size="sm"
                            variant={logFilter === level ? 'default' : 'outline'}
                            onClick={() => setLogFilter(level)}
                            className="h-7 text-xs"
                          >
                            {level}
                          </Button>
                        ))}
                      </div>
                    </div>
                    <div className="flex-1 max-w-xs">
                      <Input
                        placeholder="Search logs..."
                        value={logSearch}
                        onChange={(e) => setLogSearch(e.target.value)}
                        className="h-7 text-xs bg-gray-900 border-gray-700"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <Switch
                        id="auto-scroll"
                        checked={autoScroll}
                        onCheckedChange={setAutoScroll}
                      />
                      <Label htmlFor="auto-scroll" className="text-sm text-gray-400">Auto-scroll</Label>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Log Display */}
              <Card className="bg-gray-800/50 border-gray-700">
                <CardContent className="p-0">
                  <ScrollArea className="h-[500px]">
                    <div ref={logContainerRef} className="p-4 space-y-0">
                      {filteredLogs.map(log => (
                        <LogEntryRow key={log.id} log={log} />
                      ))}
                    </div>
                  </ScrollArea>
                </CardContent>
              </Card>
            </div>
          )}

          {/* SCADA Tab */}
          {activeTab === 'scada' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">SCADA / Observability</h2>
                  <p className="text-gray-400">Real-time industrial process monitoring</p>
                </div>
                <Badge className="bg-red-500/20 text-red-400 border-red-500/30">
                  <AlertTriangle className="h-3 w-3 mr-1" />
                  {scadaTags.filter(t => t.anomaly).length} Anomalies
                </Badge>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Tag Values */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Real-time Tag Values</CardTitle>
                    <CardDescription>Live process variables</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {scadaTags.map(tag => (
                      <SCADATagRow key={tag.id} tag={tag} />
                    ))}
                  </CardContent>
                </Card>

                {/* State Vector */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">State Vector</CardTitle>
                    <CardDescription>System state visualization</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-4 gap-3">
                      {scadaTags.slice(0, 8).map((tag, i) => (
                        <div key={tag.id} className={`aspect-square rounded-lg flex flex-col items-center justify-center p-2 ${
                          tag.anomaly ? 'bg-red-500/20 border border-red-500/30' : 'bg-gray-900'
                        }`}>
                          <span className="text-xs text-gray-500 mb-1">S{i + 1}</span>
                          <span className={`text-lg font-mono font-bold ${
                            tag.anomaly ? 'text-red-400' : 'text-emerald-400'
                          }`}>
                            {typeof tag.value === 'number' ? tag.value.toFixed(0) : tag.value}
                          </span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-4 p-3 bg-gray-900 rounded-lg">
                      <h4 className="text-xs text-gray-500 mb-2">State Vector Notation</h4>
                      <code className="text-xs text-blue-400 font-mono break-all">
                        [{scadaTags.map(t => typeof t.value === 'number' ? t.value.toFixed(1) : t.value).join(', ')}]
                      </code>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>
          )}

          {/* Knowledge Tab */}
          {activeTab === 'knowledge' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">Knowledge Graph</h2>
                  <p className="text-gray-400">Ontology and relationship visualization</p>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Node Counts */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Node Count by Type</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {knowledgeNodes.map(node => (
                      <div key={node.type} className="flex items-center justify-between">
                        <span className="text-sm text-gray-400">{node.type}</span>
                        <div className="flex items-center gap-2">
                          <Progress value={(node.count / Math.max(...knowledgeNodes.map(n => n.count))) * 100} className="w-24 h-2" />
                          <span className="text-sm font-mono text-white w-16 text-right">{node.count.toLocaleString()}</span>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                {/* Recent Additions */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Recent Additions</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {[
                      { type: 'Entity', name: 'PLC_Controller_03', time: '2 min ago' },
                      { type: 'Rule', name: 'Safety_Interlock_R01', time: '5 min ago' },
                      { type: 'Process', name: 'Batch_Sequence_B12', time: '8 min ago' },
                      { type: 'Equipment', name: 'Valve_V204', time: '12 min ago' },
                      { type: 'Constraint', name: 'Max_Pressure_C05', time: '15 min ago' },
                    ].map((item, i) => (
                      <div key={i} className="flex items-center justify-between p-2 bg-gray-900 rounded-lg">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className="text-xs">{item.type}</Badge>
                          <span className="text-sm text-white">{item.name}</span>
                        </div>
                        <span className="text-xs text-gray-500">{item.time}</span>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                {/* Federated Sync */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Federated Sync Status</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {[
                      { node: 'Node-A', status: 'synced', latency: 12 },
                      { node: 'Node-B', status: 'synced', latency: 23 },
                      { node: 'Node-C', status: 'syncing', latency: 45 },
                      { node: 'Node-D', status: 'offline', latency: 0 },
                    ].map((node, i) => (
                      <div key={i} className="flex items-center justify-between p-2 bg-gray-900 rounded-lg">
                        <div className="flex items-center gap-2">
                          <div className={`w-2 h-2 rounded-full ${
                            node.status === 'synced' ? 'bg-emerald-500' :
                            node.status === 'syncing' ? 'bg-yellow-500 animate-pulse' :
                            'bg-red-500'
                          }`} />
                          <span className="text-sm text-white">{node.node}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`text-xs ${
                            node.status === 'synced' ? 'text-emerald-400' :
                            node.status === 'syncing' ? 'text-yellow-400' :
                            'text-red-400'
                          }`}>{node.status}</span>
                          {node.status !== 'offline' && (
                            <span className="text-xs text-gray-500">{node.latency}ms</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}

          {/* Constraints Tab */}
          {activeTab === 'constraints' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">Constraint Engine</h2>
                  <p className="text-gray-400">Hard and soft constraint management</p>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <p className="text-xs text-gray-400">Block Rate</p>
                    <p className="text-xl font-bold text-emerald-400">2.3%</p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Hard Constraints */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Octagon className="h-4 w-4 text-red-400" />
                      Hard Constraints
                    </CardTitle>
                    <CardDescription>Non-negotiable safety limits</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {constraints.filter(c => c.type === 'hard').map(constraint => (
                      <div key={constraint.id} className="flex items-center justify-between p-3 bg-gray-900 rounded-lg border border-red-500/20">
                        <div className="flex items-center gap-3">
                          <div className={`w-2 h-2 rounded-full ${constraint.active ? 'bg-emerald-500' : 'bg-gray-500'}`} />
                          <div>
                            <h4 className="text-sm font-medium text-white">{constraint.name}</h4>
                            <p className="text-xs text-gray-500">{constraint.description}</p>
                          </div>
                        </div>
                        <Badge variant={constraint.violations > 0 ? 'destructive' : 'outline'} className="text-xs">
                          {constraint.violations} violations
                        </Badge>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                {/* Soft Constraints */}
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Gauge className="h-4 w-4 text-yellow-400" />
                      Soft Constraints
                    </CardTitle>
                    <CardDescription>Optimization targets</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {constraints.filter(c => c.type === 'soft').map(constraint => (
                      <div key={constraint.id} className="flex items-center justify-between p-3 bg-gray-900 rounded-lg border border-yellow-500/20">
                        <div className="flex items-center gap-3">
                          <div className={`w-2 h-2 rounded-full ${constraint.active ? 'bg-emerald-500' : 'bg-gray-500'}`} />
                          <div>
                            <h4 className="text-sm font-medium text-white">{constraint.name}</h4>
                            <p className="text-xs text-gray-500">{constraint.description}</p>
                          </div>
                        </div>
                        <Badge variant="outline" className="text-xs">
                          {constraint.violations} violations
                        </Badge>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </div>

              {/* Violation History */}
              <Card className="bg-gray-800/50 border-gray-700">
                <CardHeader>
                  <CardTitle className="text-base">Violation History</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {[
                      { time: '14:32:15', constraint: 'MIN_FLOW_RATE', severity: 'hard', action: 'Blocked action' },
                      { time: '14:28:03', constraint: 'EFFICIENCY_TARGET', severity: 'soft', action: 'Penalty applied' },
                      { time: '14:15:47', constraint: 'PRESSURE_RANGE', severity: 'hard', action: 'Blocked action' },
                      { time: '13:58:22', constraint: 'RESPONSE_TIME', severity: 'soft', action: 'Warning logged' },
                    ].map((violation, i) => (
                      <div key={i} className="flex items-center justify-between p-2 bg-gray-900 rounded-lg">
                        <div className="flex items-center gap-3">
                          <span className="text-xs font-mono text-gray-500 w-16">{violation.time}</span>
                          <Badge variant={violation.severity === 'hard' ? 'destructive' : 'outline'} className="text-xs">
                            {violation.severity}
                          </Badge>
                          <span className="text-sm text-white">{violation.constraint}</span>
                        </div>
                        <span className="text-xs text-gray-400">{violation.action}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </div>
          )}

          {/* RL Training Tab */}
          {activeTab === 'training' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold">RL Training Metrics</h2>
                  <p className="text-gray-400">Reinforcement learning performance tracking</p>
                </div>
                <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/30">
                  <Play className="h-3 w-3 mr-1" />
                  Training Active
                </Badge>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <p className="text-xs text-gray-400">Episode</p>
                    <p className="text-2xl font-bold">{rlMetrics[rlMetrics.length - 1].episode}</p>
                  </CardContent>
                </Card>
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <p className="text-xs text-gray-400">Mean Reward</p>
                    <p className="text-2xl font-bold text-emerald-400">
                      {rlMetrics[rlMetrics.length - 1].meanReward.toFixed(1)}
                    </p>
                  </CardContent>
                </Card>
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <p className="text-xs text-gray-400">Lagrange λ</p>
                    <p className="text-2xl font-bold text-yellow-400">
                      {rlMetrics[rlMetrics.length - 1].lagrangeMultiplier.toFixed(3)}
                    </p>
                  </CardContent>
                </Card>
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardContent className="p-4">
                    <p className="text-xs text-gray-400">Shield Rate</p>
                    <p className="text-2xl font-bold text-blue-400">
                      {(rlMetrics[rlMetrics.length - 1].shieldRate * 100).toFixed(1)}%
                    </p>
                  </CardContent>
                </Card>
              </div>

              <Card className="bg-gray-800/50 border-gray-700">
                <CardHeader>
                  <CardTitle className="text-base">Mean Reward Over Time</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="bg-gray-900 rounded-lg p-4">
                    <Sparkline data={rlMetrics.map(m => m.meanReward)} width={800} height={100} />
                    <div className="flex justify-between mt-2 text-xs text-gray-500">
                      <span>Episode 1</span>
                      <span>Episode {rlMetrics.length}</span>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Lagrange Multipliers</CardTitle>
                    <CardDescription>Constraint penalty coefficients</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {[
                        { name: 'Temperature', value: 0.42 },
                        { name: 'Pressure', value: 0.31 },
                        { name: 'Flow Rate', value: 0.28 },
                        { name: 'Energy', value: 0.15 },
                      ].map((lm, i) => (
                        <div key={i} className="flex items-center gap-3">
                          <span className="text-sm text-gray-400 w-24">{lm.name}</span>
                          <Progress value={lm.value * 100} className="flex-1 h-2" />
                          <span className="text-sm font-mono text-white w-12">{lm.value.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card className="bg-gray-800/50 border-gray-700">
                  <CardHeader>
                    <CardTitle className="text-base">Action Shield Stats</CardTitle>
                    <CardDescription>Safety intervention metrics</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {[
                        { name: 'Actions Blocked', value: 23, total: 1250 },
                        { name: 'Actions Modified', value: 45, total: 1250 },
                        { name: 'Warnings Issued', value: 12, total: 1250 },
                        { name: 'Safe Actions', value: 1170, total: 1250 },
                      ].map((stat, i) => (
                        <div key={i} className="flex items-center justify-between">
                          <span className="text-sm text-gray-400">{stat.name}</span>
                          <div className="flex items-center gap-2">
                            <Progress value={(stat.value / stat.total) * 100} className="w-24 h-2" />
                            <span className="text-sm font-mono text-white">{stat.value}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
