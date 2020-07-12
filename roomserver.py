#!/usr/bin/env python3

from aiohttp import web, WSMsgType, hdrs
import argparse
import asyncio
import secrets
import os
import time

class BasicRoomDaemon:
    def __init__(self, **kwargs):
        self._app = web.Application()
        self._app.add_routes([web.post('/room', self.create_room)])
        self._app.add_routes([web.get('/room/{roomid:[a-zA-Z0-9_\-]+}', self.get_room)])

        # FIXME: It would be nice to use Redis for this, but aioredis pubsub is kinda broken
        self._rooms = {}

    async def create_room(self, request):
        # FIXME: Avoid DoS here?
        roomid = secrets.token_urlsafe(16)
        topic = await request.json()
        self._rooms[roomid] = {}
        self._rooms[roomid]['clients'] = {}
        self._rooms[roomid]['topic'] = topic

        return web.HTTPCreated(headers={
            'Location': '/room/{}'.format(roomid)
            }, text=roomid)

    def send_to_client(self, room, client, msg):
        try:
            asyncio.ensure_future(self._rooms[room]['clients'][client]
                .send_json(msg))
        except:
            pass

    def send(self, me, room, msg):
        if 'target' in msg:
            self.send_to_client(room, msg['target'], msg)
        else:
            for client in self._rooms[room]['clients']:
                if client != me:
                    self.send_to_client(room, client, msg)

    # TODO: limit the number of people in a room?
    async def get_room(self, request):
        roomid = request.match_info['roomid'];
        if not roomid in self._rooms:
            return web.HTTPNotFound()

        # Return metadata only if this is a simple HTTP GET request
        uphdr = request.headers.get(hdrs.UPGRADE)
        if uphdr is None or uphdr.strip().lower() != 'websocket':
            return web.json_response({ 'topic': self._rooms[roomid]['topic'] })

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        clientid = secrets.token_urlsafe(16)
        await ws.send_json({'type': 'hello', 'payload': {
            'clientid': clientid,
            'topic': self._rooms[roomid]['topic'],
            'clients': tuple(self._rooms[roomid]['clients'].keys())
        }})
        self._rooms[roomid]['clients'][clientid] = ws
        self.send(clientid, roomid, {'type': 'clientJoin', 'payload': clientid})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.CLOSE:
                    break;
                elif msg.type == WSMsgType.TEXT:
                    try:
                        msg_obj = msg.json()
                        if msg_obj['type'] == 'setTopic':
                            # TODO: restrict who can set the topic?
                            self._rooms[roomid]['topic'] = msg_obj['payload']
                            self.send_topic(roomid)
                        else:
                            msg_obj['clientid'] = clientid
                            self.send(clientid, roomid, msg_obj)

                    except:
                        await ws.close()

        finally:
            self.clean_up_client(roomid, clientid)
            return ws

    def clean_up_client(self, roomid, clientid):
        del self._rooms[roomid]['clients'][clientid]
        if len(self._rooms[roomid]['clients']) == 0:
            del self._rooms[roomid]
        else:
            self.send(clientid, roomid, {'type': 'clientPart', 'payload': clientid})

    def send_topic(self, roomid):
        self.send(None, roomid, {
            'type': 'setTopic',
            'payload': self._rooms[roomid]['topic']
        })

    def run(self, path):
        web.run_app(self._app, path=path)

def main():
    arg_parser = argparse.ArgumentParser(description='Basic Room Server')
    arg_parser.add_argument('-U', '--path', help='Unix file system path to serve on.', default='/tmp/room_0.sock')
    args = arg_parser.parse_args()

    daemon = BasicRoomDaemon(**vars(args))
    daemon.run(args.path)

if __name__ == '__main__':
    main()