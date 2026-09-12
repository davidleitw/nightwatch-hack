import test from 'node:test';
import assert from 'node:assert/strict';
import {focusedGraph, retainLog} from '../src/investigation-data.js';

const graph = {
  nodes: [
    {id: 'shop-checkout-request', status: 'ok'},
    {id: 'shop-cart-prepare', status: 'ok'},
    {id: 'shop-cart-abort', status: 'failing'},
    {id: 'shop-catalog-lookup', status: 'warning'},
  ],
  edges: [
    {from: 'shop-checkout-request', to: 'shop-cart-prepare'},
    {from: 'shop-checkout-request', to: 'shop-cart-abort'},
  ],
};

test('focused view retains all unhealthy nodes and only real visible edges', () => {
  const view = focusedGraph(graph);
  assert.deepEqual(view.nodes.map(n => n.id), ['shop-checkout-request', 'shop-cart-abort', 'shop-catalog-lookup']);
  assert.deepEqual(view.edges, [graph.edges[1]]);
  assert.equal(graph.nodes.length, 4);
  assert.equal(focusedGraph(graph, {all: true}), graph);
});

test('search and explicit selection reveal detail nodes; other topologies stay intact', () => {
  assert.equal(focusedGraph(graph, {search: 'prepare'}).nodes.length, 4);
  assert.equal(focusedGraph(graph, {selected: 'shop-cart-prepare'}).nodes.length, 4);
  const custom = {nodes: [{id: 'custom-service', status: 'ok'}], edges: []};
  assert.equal(focusedGraph(custom), custom);
});

test('long log bursts remain bounded and duplicate event IDs are scoped by monitor', () => {
  const logs = new Map();
  for (let i = 0; i < 10000; i++) retainLog(logs, {monitor_id: 'one', event_id: String(i)});
  assert.equal(logs.size, 200);
  assert.equal([...logs.values()][0].event_id, '9800');
  retainLog(logs, {monitor_id: 'one', event_id: '9999'});
  assert.equal(logs.size, 200);
  retainLog(logs, {monitor_id: 'two', event_id: '9999'});
  assert.equal(logs.size, 200);
  assert.equal([...logs.values()].at(-1).monitor_id, 'two');
});
