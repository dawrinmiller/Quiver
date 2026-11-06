// Find the event type selector and the two interviewer contact fields.
const eventType = document.getElementById('id_event_type');
const contactInputs = [
    'id_interviewer_name',
    'id_interviewer_email'
].map(id => document.getElementById(id));

// Show and enable interviewer contact fields only for interviews.
function showInterviewContacts() {
    contactInputs.forEach(input => {
        input.closest('p').hidden = eventType.value !== 'interview';
        input.disabled = eventType.value !== 'interview';
    });
}

// Update the contact fields whenever the event type changes.
eventType.addEventListener('change', showInterviewContacts);

// Set the correct field visibility when the page first loads.
showInterviewContacts();
