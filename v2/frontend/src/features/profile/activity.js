export function emptyActivity() {
  return {
    summary: {
      currentMonthCompleted: 0,
      totalCompleted: 0,
      totalDurationMinutes: 0,
      activeDays: 0,
    },
    overview: {
      averageDurationMinutes: 0,
      favoriteRoom: null,
      favoriteTag: null,
    },
  };
}

export function activityDuration(totalMinutes) {
  const minutes = Math.max(0, Number(totalMinutes || 0));
  if (minutes < 60) return { value: minutes, unit: "分钟" };
  const hours = minutes / 60;
  return { value: Number.isInteger(hours) ? hours : Number(hours.toFixed(1)), unit: "小时" };
}
