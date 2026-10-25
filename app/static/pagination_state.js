/**
 * Pure pagination state helpers for the inbox list.
 * Keeps loadedOffset equal to the number of currently loaded API rows so
 * delete + "Load more" does not skip the next page.
 *
 * Works in the browser (window.LifeInboxPagination) and in Node (module.exports)
 * without a build step.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.LifeInboxPagination = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  const PAGE_SIZE = 100;

  function createState(pageSize = PAGE_SIZE) {
    return {
      pageSize,
      loadedOffset: 0,
      loadedItems: [],
      hasMore: false,
      listRequestInFlight: false
    };
  }

  function resetForReload(state) {
    state.loadedOffset = 0;
    state.loadedItems = [];
    state.hasMore = false;
  }

  /**
   * Attempt to start a list request. Returns false if one is already running
   * (caller should ignore repeated Load more / overlapping reloads).
   */
  function beginListRequest(state) {
    if (state.listRequestInFlight) {
      return false;
    }
    state.listRequestInFlight = true;
    return true;
  }

  function endListRequest(state) {
    state.listRequestInFlight = false;
  }

  /**
   * Apply a successful page fetch.
   * @param {object} state
   * @param {Array} rows - API rows for this page (all item types)
   * @param {{append?: boolean}} options
   * @returns {{textNotes: Array, hasMore: boolean}}
   */
  function applyFetchedPage(state, rows, {append = false} = {}) {
    if (!append) {
      resetForReload(state);
    }
    const textNotes = rows.filter(item => item.item_type === 'text');
    state.loadedItems = append
      ? state.loadedItems.concat(textNotes)
      : textNotes;
    state.loadedOffset += rows.length;
    state.hasMore = rows.length === state.pageSize;
    return {textNotes, hasMore: state.hasMore};
  }

  /**
   * After a loaded note is successfully deleted on the server, drop it from
   * local state and decrement the offset so the next page request aligns with
   * the shifted API row order.
   * @returns {boolean} true if the note was in the loaded collection
   */
  function afterLoadedNoteDeleted(state, noteId) {
    const index = state.loadedItems.findIndex(item => item.id === noteId);
    if (index === -1) {
      return false;
    }
    state.loadedItems.splice(index, 1);
    if (state.loadedOffset > 0) {
      state.loadedOffset -= 1;
    }
    return true;
  }

  /**
   * Replace a loaded note in place after a successful edit.
   * Does not change offset or hasMore.
   */
  function afterLoadedNoteUpdated(state, updatedNote) {
    const index = state.loadedItems.findIndex(
      item => item.id === updatedNote.id
    );
    if (index === -1) {
      return false;
    }
    state.loadedItems[index] = updatedNote;
    return true;
  }

  return {
    PAGE_SIZE,
    createState,
    resetForReload,
    beginListRequest,
    endListRequest,
    applyFetchedPage,
    afterLoadedNoteDeleted,
    afterLoadedNoteUpdated
  };
});
