// Run against an existing preview with an available workshop and stocked catalog.
// Stops at review: never creates a reservation, sends email or submits payment.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {chromium} from 'playwright';
import {expect} from '@playwright/test';

const [url, output] = process.argv.slice(2);
assert(url && output, 'Usage: node scripts/workshop_ui_smoke.mjs <workshop-start-url> <screenshot-directory>');
await mkdir(output, {recursive:true});
const browser = await chromium.launch();
try {
  for (const [width, height] of [[1440,900], [768,1024], [1024,768], [390,844]]) {
    const context = await browser.newContext({viewport:{width,height}, hasTouch:width < 1440});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    const days = page.locator('#dates button');
    await expect(days.first()).toBeVisible();
    const bounds = await days.evaluateAll(nodes => nodes.map(node => {
      const {left,right,top,bottom} = node.getBoundingClientRect();
      return {left,right,top,bottom};
    }));
    for (let i = 0; i < bounds.length; i++) for (let j = i + 1; j < bounds.length; j++) {
      const a = bounds[i], b = bounds[j];
      assert(!(a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom), 'Calendar day buttons overlap');
    }
    const selected = page.locator('#dates [aria-pressed=true]');
    const circle = await selected.evaluate(node => {
      const style = getComputedStyle(node, '::before');
      return {width:parseFloat(style.width), height:parseFloat(style.height), available:node.getBoundingClientRect().width};
    });
    assert(circle.width > 0 && Math.abs(circle.width-circle.height) < 1 && circle.width <= circle.available+1);
    await page.locator('.calendar').screenshot({path:`${output}/calendar-${width}.png`});
    await page.locator('#times button:not(:disabled)').first().click();
    await page.locator('#start-booking').click();
    const chooseURL = page.url();
    const catalog = JSON.parse(await page.locator('#catalog-data').textContent());
    const ageSelector = `[data-field="${catalog.age_group}"] select`;
    const fillContact = async () => {
      await page.getByLabel('Your name',{exact:true}).fill('UI verification');
      await page.getByLabel('Email',{exact:true}).fill('verification@example.invalid');
      await page.getByLabel('Phone',{exact:true}).fill('2025550123');
      await page.locator('[name=policy_ack]').check();
    };
    await page.getByLabel('Participant name',{exact:true}).fill('Riley');
    await page.locator(ageSelector).selectOption({label:'Adult'});
    await fillContact();
    const review = page.getByRole('button',{name:'Review booking'});
    await review.click();
    await expect(page.locator('.selection-error')).toHaveText('Choose a design for this participant.');
    await expect(page.locator('.design-option').first()).toBeFocused();
    await page.locator('.design-option').first().click();
    await review.click();
    await expect(page.locator('.selection-error')).toBeVisible();
    await expect(page.locator('.selection-error')).toHaveText('Choose a color for this participant.');
    await expect(page.locator('.color-option:not(:disabled)').first()).toBeFocused();
    assert.equal(page.url(), chooseURL);
    await page.screenshot({path:`${output}/color-error-${width}.png`,fullPage:true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Horizontal overflow');
    await page.locator('.color-option:not(:disabled)').first().click();
    await expect(page.locator('.selection-error')).toBeHidden();
    await review.click();
    await expect(page.locator('[data-booking-step="3"]')).toHaveAttribute('aria-current','step');
    await page.goto(chooseURL);
    if (await page.locator('#choose-later').count()) {
      await page.locator('#choose-later').check();
      const age = page.locator(ageSelector);
      if (await age.isVisible()) await age.selectOption({label:'Adult'});
      await fillContact();
      await page.getByRole('button',{name:'Review booking'}).click();
      await expect(page.locator('[data-booking-step="3"]')).toHaveAttribute('aria-current','step');
    } else {
      throw new Error('Choose-later regression requires a session outside the selection deadline.');
    }
    assert.deepEqual(errors, []);
    console.log(`${width}x${height}: calendar, visible validation, corrected selection and choose-later review passed`);
    await context.close();
  }
} finally {
  await browser.close();
}
