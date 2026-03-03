import { NextRequest, NextResponse } from 'next/server'

/**
 * KISWARM v4.9 API Proxy Route
 * Proxies requests to the KISWARM Sentinel API at localhost:11436
 */

const KISWARM_API_BASE = process.env.KISWARM_API_URL || 'http://localhost:11436'
const KISWARM_TIMEOUT = 5000 // 5 seconds timeout

interface KiswarmResponse {
  status: string
  [key: string]: unknown
}

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeout = KISWARM_TIMEOUT): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)
  
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    })
    clearTimeout(timeoutId)
    return response
  } catch (error) {
    clearTimeout(timeoutId)
    throw error
  }
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const endpoint = searchParams.get('endpoint') || 'health'
  
  try {
    const response = await fetchWithTimeout(`${KISWARM_API_BASE}/${endpoint}`)
    
    if (!response.ok) {
      return NextResponse.json(
        { 
          status: 'error', 
          error: `API returned ${response.status}`,
          endpoint,
          timestamp: new Date().toISOString()
        },
        { status: response.status }
      )
    }
    
    const data: KiswarmResponse = await response.json()
    return NextResponse.json({
      ...data,
      _proxy: {
        timestamp: new Date().toISOString(),
        source: 'kiswarm-proxy'
      }
    })
  } catch (error) {
    // Return mock data if API is unavailable
    return NextResponse.json({
      status: 'mock',
      endpoint,
      error: error instanceof Error ? error.message : 'API unavailable',
      timestamp: new Date().toISOString(),
      mock: true,
      data: getMockData(endpoint)
    })
  }
}

export async function POST(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const endpoint = searchParams.get('endpoint') || ''
  
  let body: Record<string, unknown> = {}
  try {
    body = await request.json()
  } catch {
    // Empty body is okay for some endpoints
  }
  
  try {
    const response = await fetchWithTimeout(
      `${KISWARM_API_BASE}/${endpoint}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      }
    )
    
    if (!response.ok) {
      return NextResponse.json(
        { 
          status: 'error', 
          error: `API returned ${response.status}`,
          endpoint,
          timestamp: new Date().toISOString()
        },
        { status: response.status }
      )
    }
    
    const data: KiswarmResponse = await response.json()
    return NextResponse.json({
      ...data,
      _proxy: {
        timestamp: new Date().toISOString(),
        source: 'kiswarm-proxy'
      }
    })
  } catch (error) {
    return NextResponse.json({
      status: 'mock',
      endpoint,
      error: error instanceof Error ? error.message : 'API unavailable',
      timestamp: new Date().toISOString(),
      mock: true,
      requestBody: body,
      data: getMockPostResponse(endpoint, body)
    })
  }
}

// Mock data for when the KISWARM API is not available
function getMockData(endpoint: string): Record<string, unknown> {
  const mockResponses: Record<string, Record<string, unknown>> = {
    'health': {
      status: 'active',
      service: 'KISWARM-SENTINEL-BRIDGE',
      version: '4.9',
      port: 11436,
      modules: 45,
      endpoints: 148,
      uptime: 86400,
      timestamp: new Date().toISOString(),
    },
    'sentinel/status': {
      system: 'KISWARM-SENTINEL-v4.9',
      status: 'operational',
      uptime: 86400,
      stats: {
        extractions: 15847,
        debates: 3421,
        searches: 8923,
        firewall_scans: 4521,
        firewall_blocked: 23,
      },
      timestamp: new Date().toISOString(),
    },
    'plc/stats': {
      status: 'success',
      stats: {
        total_parses: 156,
        cache_hits: 134,
        avg_parse_time_ms: 12.4,
        pid_blocks_found: 89,
        interlocks_found: 234,
      },
    },
    'scada/state': {
      status: 'success',
      state: {
        timestamp: new Date().toISOString(),
        tag_count: 48,
        anomaly_count: 2,
        features: {
          temperature: { mean: 342.5, variance: 12.3, trend: 'stable' },
          pressure: { mean: 15.7, variance: 0.8, trend: 'rising' },
          flow: { mean: 1245, variance: 156, trend: 'stable' },
        },
      },
    },
    'scada/anomalies': {
      status: 'success',
      anomalies: [
        { tag: 'Valve_Position_V12', z_score: 3.2, value: 78.5 },
        { tag: 'Conductivity_C01', z_score: 4.1, value: 2.34 },
      ],
      count: 2,
    },
    'constraints/list': {
      status: 'success',
      constraints: [
        { id: 'OVERPRESSURE_BLOCK', type: 'hard', condition: 'pressure > 8 bar' },
        { id: 'BATTERY_CRITICAL_BLOCK', type: 'hard', condition: 'SOC < 15%' },
        { id: 'OVERTEMP_BLOCK', type: 'hard', condition: 'temperature > 95°C' },
        { id: 'HIGH_PRESSURE_WARNING', type: 'soft', condition: 'pressure > 6.5 bar' },
      ],
    },
    'constraints/stats': {
      status: 'success',
      stats: {
        total_checks: 89234,
        blocked_actions: 234,
        block_rate: 0.0026,
        hard_violations: 12,
        soft_violations: 222,
      },
    },
    'kg/stats': {
      status: 'success',
      stats: {
        total_nodes: 19417,
        pid_configs: 892,
        failure_signatures: 456,
        design_blocks: 234,
        plant_profiles: 123,
        cross_project_links: 1567,
      },
    },
    'kg/recurring-patterns': {
      status: 'success',
      patterns: [
        { symptom_set: ['pressure_drop', 'vibration'], occurrences: 4, sites: ['PLANT_A', 'PLANT_B'] },
        { symptom_set: ['temperature_spike'], occurrences: 7, sites: ['PLANT_A', 'PLANT_C', 'PLANT_D'] },
      ],
      count: 2,
    },
    'ciec-rl/stats': {
      status: 'success',
      stats: {
        episode: 15847,
        mean_reward: 0.823,
        lagrange_multipliers: [0.5, 0.3, 0.2],
        shield_rate: 0.012,
        action_bounds: {
          delta_kp: [-0.05, 0.05],
          delta_ki: [-0.05, 0.05],
          delta_kd: [-0.05, 0.05],
        },
      },
    },
    'twin/stats': {
      status: 'success',
      stats: {
        total_evaluations: 3421,
        accepted: 2890,
        rejected: 531,
        acceptance_rate: 0.845,
        avg_survival_score: 0.92,
      },
    },
    'mesh/stats': {
      status: 'success',
      stats: {
        nodes: 5,
        global_round: 234,
        byzantine_tolerance: 1,
        avg_trust: 0.94,
      },
    },
    'fuzzy/stats': {
      status: 'success',
      stats: {
        parameters: { low: 0.3, medium: 0.5, high: 0.7 },
        lyapunov_energy: 0.023,
        tune_cycles: 156,
        classification_accuracy: 0.94,
      },
    },
    'ledger/status': {
      status: 'success',
      entries: 89234,
      root: 'a1b2c3d4e5f6...',
      valid: true,
    },
    'tracker/leaderboard': {
      status: 'success',
      leaderboard: [
        { rank: 1, model: 'qwen2.5:14b', elo: 1847, reliability: 0.94, debates: 1234, win_rate: 0.67 },
        { rank: 2, model: 'llama3.1:8b', elo: 1823, reliability: 0.92, debates: 987, win_rate: 0.64 },
        { rank: 3, model: 'mistral:7b', elo: 1789, reliability: 0.89, debates: 876, win_rate: 0.61 },
      ],
    },
    'installer/scan': {
      status: 'success',
      scan: {
        os: { type: 'Linux', distro: 'Ubuntu', version: '22.04' },
        hardware: { cpu_cores: 8, ram_gb: 32, disk_free_gb: 156 },
        software: { python: '3.11.4', ollama: true, qdrant: true },
        install_readiness: 'ready',
        recommended_model: 'qwen2.5:14b',
      },
    },
  }
  
  return mockResponses[endpoint] || {
    status: 'mock',
    endpoint,
    message: 'No mock data available for this endpoint',
  }
}

function getMockPostResponse(endpoint: string, _body: Record<string, unknown>): Record<string, unknown> {
  const mockResponses: Record<string, Record<string, unknown>> = {
    'sentinel/extract': {
      status: 'success',
      extraction: {
        query: 'test query',
        confidence: 0.92,
        sources: ['ollama', 'memory'],
        timestamp: new Date().toISOString(),
      },
    },
    'sentinel/debate': {
      status: 'success',
      winning_content: 'Content A',
      confidence: 0.87,
      vote_tally: { content_a: 3, content_b: 2 },
      timestamp: new Date().toISOString(),
    },
    'plc/parse': {
      status: 'success',
      result: {
        cir_nodes: 156,
        dsg_edges: 234,
        pid_blocks: [{ kp: 1.2, ki: 0.3, kd: 0.05 }],
        interlocks: [{ condition: 'ESTOP OR fault_flag' }],
      },
    },
    'scada/push': {
      status: 'success',
      ingested: 1,
    },
    'constraints/validate': {
      status: 'success',
      validation: {
        allowed: true,
        total_penalty: 0,
        hard_violations: [],
        action_after: { delta_kp: 0.02 },
      },
    },
    'kg/add-pid': {
      status: 'success',
      node_id: 'kg_node_' + Date.now(),
    },
    'rl/act': {
      status: 'success',
      scout_priority: 0.6,
      extraction_rate: 0.55,
      debate_threshold: 0.3,
      cache_eviction_rate: 0.1,
      episode: 15847,
    },
    'twin/evaluate': {
      status: 'success',
      accepted: true,
      rejection_reasons: [],
      stability_margin: 0.92,
      efficiency_gain: 0.15,
    },
    'mesh/share': {
      status: 'success',
      aggregation: 'completed',
      participating: 5,
    },
    'fuzzy/classify': {
      status: 'success',
      label: 'HIGH',
      membership: 0.87,
    },
  }
  
  return mockResponses[endpoint] || {
    status: 'mock',
    endpoint,
    message: 'POST mock response',
  }
}
