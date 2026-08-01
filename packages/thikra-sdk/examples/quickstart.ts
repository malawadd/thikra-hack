import { ThikraClient } from '../src/index.js';

const client = new ThikraClient({ apiKey: process.env.THIKRA_API_KEY!, baseUrl: process.env.THIKRA_API_URL ?? 'http://localhost:43192' });
const services = await client.services.list();
const quote = await client.quotes.create({
  service: 'verified-vertical-ad',
  input: { brief: 'Verified Arabic vertical ad for Noura Glow', language: 'ar', durationSeconds: 15, aspectRatio: '9:16' },
  buyerPrincipal: { displayName: 'Example buyer' }, buyerAgent: { name: 'Example agent' },
  maximumBudget: { amountMinor: 1000, currency: 'USD' }
});
console.log({ services: services.total, quote: quote.id, totalMinor: quote.total_minor });
