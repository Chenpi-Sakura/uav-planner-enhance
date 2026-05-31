import { reactive } from 'vue';

export interface Point {
  lat: number | string;
  lng: number | string;
  alt: number | string;
}

export interface BaseStation {
  name: string;
  lat: string;
  lng: string;
  alt: string;
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
  bases: BaseStation[];
}

export const store = reactive<StoreState>({
  selectingMode: null,
  selectingIndex: -1,
  start: { lat: '29.650000', lng: '91.130000', alt: '3650' },
  end: { lat: '29.660000', lng: '94.360000', alt: '3000' },
  waypoints: [],
  plannedPath: [],
  overlays: [],
  targetDate: '2022-07-01',
  targetTime: '08:00',
  uavSpeed: 60,
  sidebarCollapsed: false,
  chartCollapsed: true,
  isSimulating: false,
  simulationSpeed: 500,
  simulationIndex: 0,
  bases: [
    { name: '拉萨', lat: '29.650000', lng: '91.130000', alt: '3650' },
    { name: '日喀则', lat: '29.270000', lng: '88.880000', alt: '3800' },
    { name: '林芝', lat: '29.660000', lng: '94.360000', alt: '3000' },
    { name: '昌都', lat: '31.150000', lng: '97.180000', alt: '3200' }
  ]
});
