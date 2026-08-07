const http = require('http');

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'healthy' }));
  } else {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', message: 'CreepJS Gate Oracle' }));
  }
});

server.listen(3000, '0.0.0.0', () => {
  console.log('CreepJS Gate Oracle running on port 3000');
});
