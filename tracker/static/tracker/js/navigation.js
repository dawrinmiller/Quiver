// Find the controls used to open and close the navigation menu.
(() => {
    const toggle = document.getElementById('navigation-toggle');
    const sidebar = document.getElementById('main-navigation');
    const backdrop = document.getElementById('navigation-backdrop');
    const closeButton = document.getElementById('navigation-close');

    // Skip pages that do not have a navigation menu.
    if (!toggle || !sidebar || !backdrop || !closeButton)
        return;

    const header = document.querySelector('.topbar');
    const background = [
        document.querySelector('main'),
        document.getElementById('header-controls'),
        document.getElementById('navigation-shortcuts')
    ].filter(Boolean);
    const links = Array.from(sidebar.querySelectorAll('a'));
    const focusable = [toggle, closeButton, ...links];
    let isOpen = false;
    let previousInert = [];

    // Place the sidebar directly below the header.
    function updatePosition() {
        document.body.style.setProperty(
            '--navigation-top',
            `${Math.max(0, header.getBoundingClientRect().bottom)}px`
        );
    }

    // Open or close the sidebar and update keyboard access.
    function setOpen(open, restoreFocus = true) {
        // Leave the menu alone if it is already in the requested state.
        if (open === isOpen)
            return;

        isOpen = open;

        // Temporarily stop the page behind the menu from receiving clicks or focus.
        if (open) {
            updatePosition();
            previousInert = background.map(element => element.inert);
            background.forEach(element => {
                element.inert = true;
            });
        } else {
            background.forEach((element, index) => {
                element.inert = previousInert[index];
            });
        }

        // Keep the menu appearance and screen reader information in sync.
        document.body.classList.toggle('navigation-open', open);
        sidebar.inert = !open;
        sidebar.setAttribute('aria-hidden', String(!open));
        toggle.setAttribute('aria-expanded', String(open));
        toggle.setAttribute(
            'aria-label',
            open ? 'Close navigation menu' : 'Open navigation menu'
        );

        // Move focus into the open menu or back to its button after closing.
        if (open)
            closeButton.focus();
        else if (restoreFocus)
            toggle.focus();
    }

    // Open or close the menu when its button is clicked.
    toggle.addEventListener('click', () => setOpen(!isOpen));

    // Close the menu when its close button, backdrop, or a link is clicked.
    closeButton.addEventListener('click', () => setOpen(false));
    backdrop.addEventListener('click', () => setOpen(false));
    links.forEach(link => link.addEventListener('click', () => setOpen(false)));

    // Close the menu when a click lands outside it.
    document.addEventListener('click', event => {
        if (isOpen && !sidebar.contains(event.target) && !toggle.contains(event.target))
            setOpen(false);
    });

    // Let Escape close the menu and keep Tab inside the available menu controls.
    document.addEventListener('keydown', event => {
        if (!isOpen)
            return;

        // Close the menu when Escape is pressed.
        if (event.key === 'Escape') {
            event.preventDefault();
            setOpen(false);
        } else if (event.key === 'Tab') {
            const index = focusable.indexOf(document.activeElement);

            // Wrap backward from the first control to the last link.
            if (event.shiftKey && index <= 0) {
                event.preventDefault();
                focusable[focusable.length - 1].focus();
            } else if (!event.shiftKey && (index === focusable.length - 1 || index === -1)) {
                event.preventDefault();
                toggle.focus();
            }
        }
    });

    // Return focus to the menu if another part of the page tries to take it.
    document.addEventListener('focusin', event => {
        if (isOpen && !sidebar.contains(event.target) && event.target !== toggle)
            closeButton.focus();
    });

    // Keep the menu below the header when the screen or header size changes.
    window.addEventListener('resize', updatePosition);
    if (typeof ResizeObserver !== 'undefined')
        new ResizeObserver(updatePosition).observe(header);

    // Clear the open menu when leaving or returning to this page.
    window.addEventListener('pagehide', () => setOpen(false, false));
    window.addEventListener('pageshow', () => setOpen(false, false));

    // Start with the sidebar closed and reveal the working menu controls.
    sidebar.inert = true;
    sidebar.setAttribute('aria-hidden', 'true');
    document.body.classList.add('nav-ready');
    toggle.hidden = false;
    closeButton.hidden = false;
    backdrop.hidden = false;
    updatePosition();
})();
