export function createExclusiveGuard() {
  let busy = false;
  return {
    acquire() {
      if (busy) return false;
      busy = true;
      return true;
    },
    release() {
      busy = false;
    },
  };
}

export function createLatestRequestGuard() {
  let sequence = 0;
  return {
    next() {
      sequence += 1;
      return sequence;
    },
    isCurrent(requestNumber) {
      return sequence === requestNumber;
    },
  };
}

export function createLifetimeGuard() {
  let active = true;
  return {
    begin() {
      active = true;
    },
    isActive() {
      return active;
    },
    end() {
      active = false;
    },
  };
}
