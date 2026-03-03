import { NextRequest, NextResponse } from 'next/server'

/**
 * KISWARM v5.1 PLANETARY MACHINE API Proxy Route
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
      version: '5.1',
      port: 11436,
      modules: 57,
      endpoints: 360,
      uptime: 86400,
      timestamp: new Date().toISOString(),
    },
    'sentinel/status': {
      system: 'KISWARM-SENTINEL-v5.1',
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
    
    // v5.1 Solar Chase Endpoints
    'solar-chase/status': {
      status: 'success',
      coordinator: {
        compute_mode: 'active',
        handoff_state: 'idle',
        node_location: { latitude: 48.0, longitude: 11.0, timezone: 'CET' },
        energy_threshold: 98.0,
        surplus_threshold: 2.0,
      },
      solar_status: 'overcapacity',
      timestamp: new Date().toISOString(),
    },
    'solar-chase/energy': {
      status: 'success',
      energy_state: {
        battery_soc: 98.5,
        solar_input_kw: 7.8,
        load_kw: 2.5,
        grid_draw_kw: 0.0,
        surplus_kw: 5.3,
        supercap_voltage: 48.2,
      },
      timestamp: new Date().toISOString(),
    },
    'solar-chase/solar-position': {
      status: 'success',
      position: {
        azimuth: 187.4,
        elevation: 45.2,
        solar_flux: 892.5,
        is_daylight: true,
        day_length_hours: 14.5,
      },
      timestamp: new Date().toISOString(),
    },
    'solar-chase/compute-load': {
      status: 'success',
      compute_load: {
        ollama_inference_kw: 2.12,
        ciec_training_kw: 1.59,
        guard_operations_kw: 1.06,
        mesh_sync_kw: 0.53,
        total_compute_kw: 5.3,
      },
      timestamp: new Date().toISOString(),
    },
    'solar-chase/events': {
      status: 'success',
      events: [
        { event_id: 'sc_001', timestamp: new Date(Date.now() - 3600000).toISOString(), compute_allocated: 4.8, source: 'solar_overcapacity' },
        { event_id: 'sc_002', timestamp: new Date(Date.now() - 1800000).toISOString(), compute_allocated: 5.2, source: 'solar_overcapacity' },
      ],
      count: 2,
    },
    
    // Pivot Engine Endpoints
    'pivot/status': {
      status: 'success',
      engine: {
        state: 'active',
        last_evaluation: new Date().toISOString(),
        pivot_count: 156,
        total_compute_hours: 234.5,
      },
      zero_feed_in_enforced: true,
      grid_draw_events: 0,
      timestamp: new Date().toISOString(),
    },
    'pivot/decisions': {
      status: 'success',
      decisions: [
        { timestamp: new Date(Date.now() - 600000).toISOString(), action: 'activate_compute', surplus_kw: 5.3, reason: 'battery_full_solar_surplus' },
        { timestamp: new Date(Date.now() - 1200000).toISOString(), action: 'maintain_compute', surplus_kw: 4.8, reason: 'continuous_surplus' },
      ],
      count: 2,
    },
    
    // Sun Mesh Endpoints
    'sun-mesh/status': {
      status: 'success',
      mesh: {
        total_nodes: 10,
        sunlit_nodes: 5,
        active_migrations: 0,
        avg_latency_ms: 45,
      },
      global_coverage: '75%',
      timestamp: new Date().toISOString(),
    },
    'sun-mesh/sunlit-nodes': {
      status: 'success',
      nodes: [
        { node_id: 'munich-01', latitude: 48.14, longitude: 11.58, solar_flux: 892, trust: 0.95 },
        { node_id: 'london-01', latitude: 51.51, longitude: -0.13, solar_flux: 756, trust: 0.92 },
        { node_id: 'newyork-01', latitude: 40.71, longitude: -74.01, solar_flux: 623, trust: 0.94 },
      ],
      count: 3,
      timestamp: new Date().toISOString(),
    },
    'sun-mesh/migration-status': {
      status: 'success',
      migration: {
        state: 'idle',
        source_node: null,
        target_node: null,
        progress: 0,
      },
      timestamp: new Date().toISOString(),
    },
    'sun-mesh/migration-history': {
      status: 'success',
      migrations: [
        { timestamp: new Date(Date.now() - 86400000).toISOString(), from: 'munich-01', to: 'newyork-01', success: true },
        { timestamp: new Date(Date.now() - 172800000).toISOString(), from: 'newyork-01', to: 'tokyo-01', success: true },
      ],
      count: 2,
    },
    
    // Emission Tracker Endpoints
    'emission/status': {
      status: 'success',
      tracker: {
        total_events: 89234,
        total_kwh: 1234.5,
        carbon_kg: 0.0,
        zero_emission_percentage: 100.0,
        merkle_root: 'a1b2c3d4e5f67890...',
      },
      esg_compliant: true,
      timestamp: new Date().toISOString(),
    },
    'emission/events': {
      status: 'success',
      events: [
        { event_id: 'em_001', timestamp: new Date().toISOString(), kwh: 0.5, source: 'solar_overcapacity', carbon_kg: 0.0 },
        { event_id: 'em_002', timestamp: new Date(Date.now() - 300000).toISOString(), kwh: 0.8, source: 'solar_overcapacity', carbon_kg: 0.0 },
      ],
      count: 2,
    },
    'emission/merkle-root': {
      status: 'success',
      root: 'a1b2c3d4e5f67890abcdef1234567890',
      events_count: 89234,
      last_event: new Date().toISOString(),
      integrity_verified: true,
    },
    'emission/verify': {
      status: 'success',
      verification: {
        valid: true,
        events_verified: 89234,
        integrity_check: 'passed',
        last_tamper_check: new Date().toISOString(),
      },
    },
    'emission/esg-report': {
      status: 'success',
      report: {
        period: '2024-Q1',
        total_compute_kwh: 1234.5,
        carbon_emissions_kg: 0.0,
        renewable_percentage: 100.0,
        grid_draw_events: 0,
        compliance: 'full',
        certification_ready: true,
      },
      generated: new Date().toISOString(),
    },
    
    // Handoff Validator Endpoints
    'handoff-validator/status': {
      status: 'success',
      validator: {
        state: 'ready',
        validations_passed: 234,
        validations_failed: 2,
        trust_threshold: 0.7,
        max_latency_ms: 500,
      },
      timestamp: new Date().toISOString(),
    },
    'handoff-validator/rules': {
      status: 'success',
      rules: [
        { id: 'solar_surplus_check', description: 'Target has real solar surplus', active: true },
        { id: 'trust_score_check', description: 'Trust score >= 0.7', active: true },
        { id: 'latency_check', description: 'Latency <= 500ms', active: true },
        { id: 'security_cleared', description: 'LionGuard security clearance', active: true },
        { id: 'article_0_compliant', description: 'Article 0 constitutional compliance', active: true },
        { id: 'network_safe', description: 'Network path is safe', active: true },
      ],
      count: 6,
    },
    'handoff-validator/validations': {
      status: 'success',
      validations: [
        { timestamp: new Date(Date.now() - 3600000).toISOString(), target: 'newyork-01', result: 'passed', trust: 0.94 },
        { timestamp: new Date(Date.now() - 7200000).toISOString(), target: 'tokyo-01', result: 'passed', trust: 0.91 },
      ],
      count: 2,
    },
    
    // Planetary Integration Endpoints
    'planetary/status': {
      status: 'success',
      planetary: {
        mode: 'sun_chasing',
        current_region: 'Europe',
        sun_longitude: 11.5,
        next_handoff_eta: '3h 24m',
        active_nodes: 10,
        global_compute_kw: 15.7,
      },
      carbon_footprint: 0.0,
      timestamp: new Date().toISOString(),
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
