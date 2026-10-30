// Filter the existing cards and count only the cards currently visible.
const board = document.getElementById('application-board');
const boardSearch = document.getElementById('board-search');
const boardMessage = document.getElementById('board-message');
let draggedCard = null;

// Show cards that match the search and refresh the visible counts.
function filterBoard() {
    const term = boardSearch.value.trim().toLocaleLowerCase();
    let totalVisible = 0;
    // Count the matching applications in each status column.
    board.querySelectorAll('[data-column]').forEach(column => {
        let count = 0;
        // Match either the company name or job title.
        column.querySelectorAll('[data-card]').forEach(card => {
            card.hidden = !(
                card.dataset.company.toLocaleLowerCase().includes(term) ||
                card.dataset.role.toLocaleLowerCase().includes(term)
            );
            // Include only visible cards in the column count.
            if (!card.hidden) {
                count += 1;
            }
        });
        column.querySelector('[data-column-count]').textContent = count;
        column.querySelector('[data-column-empty]').hidden = count > 0;
        totalVisible += count;
    });
    document.getElementById('board-no-matches').hidden = (
        totalVisible > 0 || board.querySelectorAll('[data-card]').length === 0
    );
}

// Save first, then move the card so a rejected update never changes the columns.
async function updateBoardStatus(card, destination) {
    // Wait for the current save instead of sending the same card twice.
    if (card.dataset.saving === 'true') {
        return;
    }
    // Keep the original status and prepare the fields to send to Django.
    const form = card.querySelector('[data-status-form]');
    const selector = form.elements.status;
    const date = form.elements.date_submitted;
    const oldStatus = card.dataset.status;
    const body = new FormData(form);
    body.set('status', destination);
    card.dataset.saving = 'true';
    card.setAttribute('aria-busy', 'true');
    const controls = Array.from(form.elements);
    // Pause the controls while the save is in progress.
    controls.forEach(control => {
        control.disabled = true;
    });
    boardMessage.textContent = 'Saving status...';
    // Send the status change with the normal Django security token.
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': form.elements.csrfmiddlewaretoken.value,
                'Accept': 'application/json'
            },
            body
        });
        if (response.redirected) {
            throw new Error(
                'Your session may have expired. Sign in again and refresh the board.'
            );
        }
        const result = await response.json();
        if (!response.ok || !result.success) {
            // Reveal the form so the user can correct a missing date or other error.
            const actions = card.querySelector('[data-card-actions]');
            if (actions) {
                actions.open = true;
            }
            const errors = Object.values(result.errors || {}).flat();
            const message = errors.map(error => error.message).join(' ');
            throw new Error(
                message || 'The status could not be updated. Please try again.'
            );
        }
        // Move the card only after Django confirms the saved status.
        const column = Array.from(board.querySelectorAll('[data-column]')).find(
            item => item.dataset.column === result.status
        );
        column.querySelector('[data-card-list]').appendChild(card);
        card.dataset.status = result.status;
        selector.value = result.status;
        date.value = result.date_submitted;
        card.querySelector('[data-status-label]').textContent = result.label;
        card.querySelector('[data-date-label]').textContent = (
            result.date_submitted || 'Not submitted'
        );
        boardMessage.textContent = 'Application moved to ' + result.label + '.';
    } catch (error) {
        // Restore the selector and explain why the update could not be confirmed.
        selector.value = oldStatus;
        boardMessage.textContent = (
            error instanceof TypeError || error instanceof SyntaxError
                ? 'Could not confirm the update. Check your connection and refresh the board before trying again.'
                : error.message
        );
    } finally {
        // Restore the card controls and refresh the visible column counts.
        delete card.dataset.saving;
        card.removeAttribute('aria-busy');
        controls.forEach(control => {
            control.disabled = false;
        });
        filterBoard();
    }
}

// Keep the status form usable by keyboard and touch users.
board.querySelectorAll('[data-card]').forEach(card => {
    const form = card.querySelector('[data-status-form]');
    // Save the selected status when the user submits the card form.
    form.addEventListener('submit', event => {
        event.preventDefault();
        updateBoardStatus(card, form.elements.status.value);
    });

    // Start a drag only from the card itself, not from its links or form controls.
    card.addEventListener('dragstart', event => {
        if (
            card.dataset.saving === 'true' ||
            event.target.closest('a, input, select, button, summary')
        ) {
            event.preventDefault();
            return;
        }
        draggedCard = card;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', card.id || card.dataset.company);
        card.classList.add('dragging');
    });
    // Remove drag markers when the user releases the card.
    card.addEventListener('dragend', () => {
        draggedCard = null;
        card.classList.remove('dragging');
        board.querySelectorAll('[data-column]').forEach(column => {
            column.classList.remove('drop-target');
        });
    });
});

// Both dropping a card and submitting its selector use the same save function.
board.querySelectorAll('[data-column]').forEach(column => {
    // Highlight a column while a card is held over it.
    column.addEventListener('dragover', event => {
        if (draggedCard) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            column.classList.add('drop-target');
        }
    });
    // Remove the highlight when the card leaves the column.
    column.addEventListener('dragleave', event => {
        if (!column.contains(event.relatedTarget)) {
            column.classList.remove('drop-target');
        }
    });
    // Save a move only when the destination is a different status.
    column.addEventListener('drop', event => {
        event.preventDefault();
        column.classList.remove('drop-target');
        if (draggedCard && draggedCard.dataset.status !== column.dataset.column) {
            updateBoardStatus(draggedCard, column.dataset.column);
        }
    });
});

// Reset the search without changing any application data.
boardSearch.addEventListener('input', filterBoard);
document.getElementById('board-reset').addEventListener('click', () => {
    boardSearch.value = '';
    filterBoard();
    boardSearch.focus();
});
filterBoard();
