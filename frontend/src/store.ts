import { reactive } from 'vue';

export interface Point {
  lat: number | string;
  lng: number | string;
  alt: number | string;
}

export interface StoreState {
  selectingMode: 'start' | 'waypoint' | 'end' | null;
  selectingIndex: number;
  start: Point;
  end: Point;
  waypoints: Point[];
  plannedPath: Point[];
  overlays: string[];
  targetDate: string;
  targetTime: string;
  uavSpeed: number;
  sidebarCollapsed: boolean;
  chartCollapsed: boolean;
  isSimulating: boolean;
  simulationSpeed: number;
  simulationIndex: number;
}

export const store = reactive<StoreState>({
  selectingMode: null,
  selectingIndex: -1,
  start: { lat: '', lng: '', alt: '' },
  end: { lat: '', lng: '', alt: '' },
  waypoints: [],
  plannedPath: [],
  overlays: [],
  targetDate: '2022-05-30',
  targetTime: '08:00',
  uavSpeed: 60,
  sidebarCollapsed: false,
  chartCollapsed: true,
  isSimulating: false,
  simulationSpeed: 500,
  simulationIndex: 0
});
