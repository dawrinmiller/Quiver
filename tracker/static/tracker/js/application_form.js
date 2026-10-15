// Show optional rejection feedback, keeping saved feedback editable after a status change.
const statusSelect = document.getElementById('id_status');
const feedback = document.getElementById('id_rejection_feedback');
const dateSubmitted = document.getElementById('id_date_submitted');
const hasFeedback = feedback.value.trim().length > 0;

function updateApplicationFields() {
    feedback.closest('p').hidden = statusSelect.value !== 'rejected' && !hasFeedback;
    dateSubmitted.required = statusSelect.value !== 'preparing';
}

statusSelect.addEventListener('change', updateApplicationFields);
updateApplicationFields();
