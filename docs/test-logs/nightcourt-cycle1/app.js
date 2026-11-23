document.addEventListener('DOMContentLoaded', () => {
    const menuToggle = document.getElementById('menu-toggle');
    const menu = document.getElementById('menu');

    menuToggle.addEventListener('click', () => {
        const isExpanded = menuToggle.getAttribute('aria-expanded') === 'true';
        menuToggle.setAttribute('aria-expanded', !isExpanded);
        menu.hidden = isExpanded;
    });

    const bookingSummary = document.getElementById('booking-summary');
    const sessionButtons = document.querySelectorAll('.book');

    sessionButtons.forEach(button => {
        button.addEventListener('click', () => {
            const session = button.getAttribute('data-session');
            bookingSummary.textContent = `You have booked: ${session}`;
        });
    });

    const membershipButtons = document.querySelectorAll('.join');
    const membershipFeedback = document.getElementById('membership-feedback');

    membershipButtons.forEach(button => {
        button.addEventListener('click', () => {
            membershipFeedback.textContent = 'Thank you for joining NIGHT COURT!';
        });
    });

    const contactForm = document.getElementById('contact-form');
    const contactFeedback = document.getElementById('contact-feedback');

    contactForm.addEventListener('submit', (event) => {
        event.preventDefault();
        contactFeedback.textContent = 'Your message has been sent!';
    });
});