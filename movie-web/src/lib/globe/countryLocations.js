// Approximate camera/label anchors for regions omitted by the low-resolution
// boundary asset. These are points, never fabricated country polygons.
// SGP/HKG: geographic coordinates in the World Factbook archives.
// GLP: https://www.geonames.org/6690364/guadeloupe.html
const POINTS = Object.freeze({
  SGP: {lat: 1.3667, lng: 103.8},
  HKG: {lat: 22.25, lng: 114.1667},
  GLP: {lat: 16.24988, lng: -61.58365},
});

export function countryLocation(iso3, featureMap) {
  const point = featureMap.get(iso3)?.properties || POINTS[iso3];
  return point && Number.isFinite(point.lat) && Number.isFinite(point.lng)
    ? point : null;
}
