export function calculateLegacyTotal(values) {
  const cleanValues = values.filter(Boolean);
  return cleanValues.reduce((total, value) => total + value, 0);
}
