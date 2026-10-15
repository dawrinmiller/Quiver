// Match both company/role search and the selected status without changing account totals.
const search = document.getElementById('application-search');
const statusFilter = document.getElementById('status-filter');
const rows = Array.from(document.querySelectorAll('[data-application-row]'));

function filterApplications() {
    const term = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    rows.forEach(row => {
        const matchesText = (
            row.dataset.company.toLocaleLowerCase().includes(term) ||
            row.dataset.role.toLocaleLowerCase().includes(term)
        );
        const matchesStatus = (
            !statusFilter.value ||
            row.dataset.status === statusFilter.value
        );
        row.hidden = !(matchesText && matchesStatus);
        if (!row.hidden)
            visible += 1;
    });
    document.getElementById('no-matches').hidden = visible > 0 || rows.length === 0;
}

search.addEventListener('input', filterApplications);
statusFilter.addEventListener('change', filterApplications);
filterApplications();
