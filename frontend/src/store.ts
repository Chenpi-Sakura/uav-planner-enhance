import { reactive } from 'vue';

export interface Point {
  lat: number | string;
  lng: number | string;
  alt: number | string;
}

export const store = reactive({
  selectingMode: null as 'start' | 'waypoint' | 'end' | null,
  selectingIndex: -1,
  start: { lat: '', lng: '', alt: '' } as Point,
  end: { lat: '', lng: '', alt: '' } as Point,
  waypoints: [] as Point[],
  plannedPath: [] as Point[], // <--- added for dense planned path
  overlays: [] as string[]
});
