// Test interaction and accessibility behavior without adding a browser dependency.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Build a small pretend page for testing menu actions.
function fixture(authenticated = true) {
    const doc = {
        listeners: {},
        activeElement: null
    };
    // Create a pretend page element that remembers clicks and focus.
    function element() {
        const classes = new Set();
        return {
            hidden: true,
            inert: false,
            attributes: {},
            listeners: {},
            style: {
                setProperty(name, value) {
                    this[name] = value;
                }
            },
            classList: {
                add(name) {
                    classes.add(name);
                },
                toggle(name, value) {
                    // Add or remove a class to match the requested state.
                    if (value)
                        classes.add(name);
                    else
                        classes.delete(name);
                },
                contains(name) {
                    return classes.has(name);
                }
            },
            addEventListener(name, handler) {
                this.listeners[name] = handler;
            },
            setAttribute(name, value) {
                this.attributes[name] = value;
            },
            contains(target) {
                return target === this;
            },
            focus() {
                doc.activeElement = this;
            }
        };
    }
    const toggle = element(),
        sidebar = element(),
        backdrop = element(),
        close = element();
    const main = element(),
        controls = element(),
        header = element();
    const links = Array.from({
        length: 6
    }, element);
    sidebar.contains = target => [sidebar, close, ...links].includes(target);
    sidebar.querySelectorAll = () => links;
    header.getBoundingClientRect = () => ({
        bottom: header.bottom
    });
    header.bottom = 86;
    doc.body = element();
    const ids = {
        'navigation-toggle': toggle,
        'main-navigation': sidebar,
        'navigation-backdrop': backdrop,
        'navigation-close': close,
        'header-controls': controls
    };
    doc.getElementById = id => authenticated ? ids[id] : null;
    doc.querySelector = selector => selector === 'main' ? main : header;
    doc.addEventListener = (name, handler) => {
        doc.listeners[name] = handler;
    };
    const window = {
        listeners: {},
        addEventListener(name, handler) {
            this.listeners[name] = handler;
        }
    };
    vm.runInNewContext(
        fs.readFileSync(path.join(__dirname, 'static/tracker/js/navigation.js'), 'utf8'),
        {
            document: doc,
            window
        }
    );
    return {
        doc,
        window,
        toggle,
        sidebar,
        backdrop,
        close,
        main,
        controls,
        links,
        header
    };
}
// Send a pretend keyboard action to the menu.
function key(f, key, shiftKey = false) {
    const event = {
        key,
        shiftKey,
        prevented: false,
        preventDefault() {
            this.prevented = true;
        }
    };
    f.doc.listeners.keydown(event);
    return event;
}

// Check that opening and closing the menu restores focus and scrolling.
test('starts closed; hamburger opens and toggles closed with focus and scrolling restored', () => {
    const f = fixture();
    assert.equal(f.toggle.hidden, false);
    assert.equal(f.sidebar.inert, true);
    assert.equal(f.sidebar.attributes['aria-hidden'], 'true');
    f.toggle.listeners.click();
    assert.equal(f.toggle.attributes['aria-expanded'], 'true');
    assert.equal(f.sidebar.inert, false);
    assert.equal(f.main.inert, true);
    assert.equal(f.controls.inert, true);
    assert.equal(f.doc.activeElement, f.close);
    assert.equal(f.doc.body.classList.contains('navigation-open'), true);
    f.toggle.listeners.click();
    assert.equal(f.toggle.attributes['aria-expanded'], 'false');
    assert.equal(f.doc.activeElement, f.toggle);
    assert.equal(f.main.inert, false);
    assert.equal(f.doc.body.classList.contains('navigation-open'), false);
});

// Check every way the user can close the menu.
test('X, backdrop, outside click, Escape and every destination dismiss navigation', () => {
    const dismissals = [
        f => f.close.listeners.click(),
        f => f.backdrop.listeners.click(),
        f => f.doc.listeners.click({
            target: f.header
        }),
        f => key(f, 'Escape'),
        ...Array.from({
            length: 6
        }, (_, index) => f => f.links[index].listeners.click())
    ];
    for (const dismiss of dismissals) {
        const f = fixture();
        f.toggle.listeners.click();
        dismiss(f);
        assert.equal(f.sidebar.inert, true);
        assert.equal(f.toggle.attributes['aria-expanded'], 'false');
        assert.equal(f.doc.activeElement, f.toggle);
    }
});

// Check that keyboard focus wraps within the menu and toggle and obscured controls cannot keep focus.
test('keyboard focus wraps within the menu and toggle; obscured controls cannot keep focus', () => {
    const f = fixture();
    f.toggle.listeners.click();
    f.links[5].focus();
    assert.equal(key(f, 'Tab').prevented, true);
    assert.equal(f.doc.activeElement, f.toggle);
    assert.equal(key(f, 'Tab', true).prevented, true);
    assert.equal(f.doc.activeElement, f.links[5]);
    f.doc.listeners.focusin({
        target: f.main
    });
    assert.equal(f.doc.activeElement, f.close);
    f.doc.listeners.click({
        target: f.links[0]
    });
    assert.equal(f.toggle.attributes['aria-expanded'], 'true');
    key(f, 'Escape');
    assert.equal(key(f, 'Tab').prevented, false);
});

// Check that resizing follows header height and page exit clears the overlay and preserves prior inert state.
test('resizing follows header height; page exit clears the overlay and preserves prior inert state', () => {
    const f = fixture();
    f.controls.inert = true;
    f.toggle.listeners.click();
    f.header.bottom = 130;
    f.window.listeners.resize();
    assert.equal(f.doc.body.style['--navigation-top'], '130px');
    f.window.listeners.pagehide();
    assert.equal(f.doc.body.classList.contains('navigation-open'), false);
    assert.equal(f.main.inert, false);
    assert.equal(f.controls.inert, true);
});

// Check that script safely skips unauthenticated layouts.
test('script safely skips unauthenticated layouts', () => {
    assert.doesNotThrow(() => fixture(false));
});
