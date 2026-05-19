const activityLog = [];
const streamLog = [];
const subscribers = new Set();

const notifySubscribers = () => {
  subscribers.forEach((listener) => {
    try {
      listener(activityLog);
    } catch (error) {
      console.error("LoggerService subscriber error:", error);
    }
  });
};

const generateTransactionID = () => Math.random().toString(36).slice(2, 11);

const findTransactionByID = (transactionID) =>
  activityLog.find((transaction) => transaction.id === transactionID);

export const logActivity = {
  start: (title) => {
    const transactionID = generateTransactionID();
    activityLog.push({
      id: transactionID,
      title,
      startTime: new Date().toISOString(),
      logEntries: [],
    });
    notifySubscribers();
    return transactionID;
  },
  addEntry: (transactionID, style, message, data) => {
    const transaction = findTransactionByID(transactionID);
    if (!transaction) {
      return;
    }
    transaction.logEntries.push({
      timestamp: new Date().toISOString(),
      style,
      message,
      data,
    });
    notifySubscribers();
  },
  end: (transactionID) => {
    const transaction = findTransactionByID(transactionID);
    if (!transaction) {
      return;
    }
    transaction.endTime = new Date().toISOString();
    notifySubscribers();
  },
  getActivityLog: () => activityLog,
  pushStreamEvent: (event) => {
    const streamEvent = {
      id: event?.id || generateTransactionID(),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      ...event,
    };
    streamLog.push(streamEvent);
    if (streamLog.length > 20) {
      streamLog.splice(0, streamLog.length - 20);
    }
    notifySubscribers();
    return streamEvent.id;
  },
  updateStreamEvent: (streamEventId, patch) => {
    const target = streamLog.find((event) => event.id === streamEventId);
    if (!target) {
      return;
    }
    Object.assign(target, patch || {}, {
      updatedAt: new Date().toISOString(),
    });
    notifySubscribers();
  },
  getStreamLog: () => streamLog,
  subscribe: (listener) => {
    if (typeof listener !== "function") {
      return () => {};
    }
    subscribers.add(listener);
    return () => subscribers.delete(listener);
  },
};
