Docker:

# Verify Docker
docker --version
docker run hello-world

# Build image
docker build -t mywebapp .

# View images
docker images

# Run container
docker run -d -p 8080:80 --name mycontainer mywebapp

# View running containers
docker ps

# Login to Docker Hub
docker login

# Tag image
docker tag mywebapp:latest YOUR_USERNAME/mywebapp:v1

# Push image
docker push YOUR_USERNAME/mywebapp:v1

# Stop and remove local container
docker stop mycontainer
docker rm mycontainer

# Remove local image references
docker rmi YOUR_USERNAME/mywebapp:v1
docker rmi mywebapp:latest

# Pull image
docker pull YOUR_USERNAME/mywebapp:v1

# Run downloaded image
docker run -d -p 8080:80 --name newcontainer YOUR_USERNAME/mywebapp:v1

# Verify
docker ps


kubernates:


minikube version
kubectl version --client

minikube start --driver=docker
minikube status

kubectl get nodes

kubectl create deployment mynginx --image=nginx

kubectl get deployments
kubectl get pods
kubectl describe pods

kubectl expose deployment mynginx --type=NodePort --port=80 --target-port=80

kubectl scale deployment mynginx --replicas=4

kubectl get pods
kubectl get deployments
kubectl get services

kubectl port-forward svc/mynginx 8081:80


Nagios:

docker pull jasonrivers/nagios:latest

docker images

docker run -d -p 8888:80 --name nagiosdemo jasonrivers/nagios:latest

docker ps

docker logs nagiosdemo

docker exec -it nagiosdemo tail -f /opt/nagios/var/nagios.log

docker stop nagiosdemo

docker start nagiosdemo

AWS:

>sudo apt update
>sudo apt-get install docker.io
>sudo apt install git
>sudo apt install nano
>git clone <GitWebMaven-url>
>creating dockerfile(already created before same can be used)
>sudo docker build -t <image-name> .
>sudo docker run -d -p 80:80 <image-name>
